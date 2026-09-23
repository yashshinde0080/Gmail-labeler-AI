"""
APScheduler job definitions.

Two recurring jobs:
    1. process_new_emails()  — main pipeline, every POLL_INTERVAL_MINUTES
    2. retry_failed_emails() — retry queue, runs after main job

The scheduler is started inside main.py's lifespan handler so that it
shares the same process as FastAPI and shuts down cleanly on SIGTERM.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from googleapiclient.errors import HttpError

from app.ai.classifier import classify_email
from app.config import get_settings
from app.database.crud import (
    create_or_update_retry,
    create_processed_email,
    defer_rate_limited,
    delete_retry,
    get_pending_retries,
    is_already_processed,
)
from app.database.db import get_session
from app.database.models import ProcessedEmail
from app.gmail.auth import get_credentials
from app.gmail.labels import (
    apply_label_to_message,
    create_label,
    fetch_and_sync_labels,
    find_best_label_match,
)
from app.gmail.messages import get_message_detail
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# ── Scheduler instance ────────────────────────────────────────────────────────
_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    """Return the global scheduler, creating it if necessary."""
    global _scheduler  # noqa: PLW0603
    if _scheduler is None:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

        from app.database.db import engine

        jobstores = {"default": SQLAlchemyJobStore(engine=engine)}
        _scheduler = BackgroundScheduler(jobstores=jobstores, timezone="UTC")
    return _scheduler


# ── Main Pipeline ─────────────────────────────────────────────────────────────


def process_new_emails() -> dict[str, int]:
    """
    Full email processing pipeline (Cron / Manual Sync):

        1. Ensure Gmail credentials are valid.
        2. Get current historyId. If we don't have a baseline, save it and exit (do not process old emails).
        3. Sync the label cache.
        4. Fetch new messages via History API.
        5. For each message:
            a. Download details.
            b. Classify with Groq AI.
            c. Resolve / create Gmail label.
            d. Apply label.
            e. Star or archive according to AI decision.
            f. Store result in SQLite.

    Returns a summary dict used by the /sync endpoint.
    """
    logger.info("Starting email processing cycle (History sync)")
    summary = {
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "labels_created": 0,
        "deferred": 0,
    }

    # ── Step 1: Auth ──────────────────────────────────────────────────────
    try:
        get_credentials()
    except Exception as exc:
        logger.error("Authentication failed — aborting cycle", error=str(exc))
        return summary

    from app.database.crud import get_setting, set_setting
    from app.gmail.messages import get_new_messages_from_history
    from app.gmail.service import get_gmail_service

    with get_session() as db:
        last_history_id = get_setting(db, "last_history_id")

    service = get_gmail_service()

    # ── Step 2: Establish Baseline if missing ─────────────────────────────
    if not last_history_id:
        try:
            profile = service.users().getProfile(userId="me").execute()
            current_history_id = profile.get("historyId")
            if current_history_id:
                with get_session() as db:
                    set_setting(
                        db,
                        "last_history_id",
                        str(current_history_id),
                        "Latest Gmail History ID",
                    )
                logger.info(
                    "Baseline historyId established. Skipping old emails.",
                    history_id=current_history_id,
                )
            return summary
        except Exception as exc:
            logger.error(
                "Failed to fetch Gmail profile for baseline historyId", error=str(exc)
            )
            return summary

    # ── Step 3: Fetch new messages from History API ───────────────────────
    try:
        message_ids, latest_history_id = get_new_messages_from_history(last_history_id)
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            # Cached credentials were rejected (e.g. revoked refresh token).
            # Drop the cache so the next cycle rebuilds them from the DB —
            # otherwise every poll fails identically until restart.
            from app.gmail.auth import invalidate_credentials

            invalidate_credentials()
            logger.error(
                "Gmail rejected credentials — cache cleared; will retry "
                "with a fresh token next cycle (visit /login if this "
                "error persists)",
                status=exc.resp.status,
            )
            return summary
        logger.error("Failed to fetch history deltas", error=str(exc))
        return summary
    except Exception as exc:
        logger.error("Failed to fetch history deltas", error=str(exc))
        return summary

    if latest_history_id:
        with get_session() as db:
            set_setting(db, "last_history_id", str(latest_history_id))

    if not message_ids:
        logger.info("No new messages since last sync")
        return summary

    # ── Step 4: Sync labels ───────────────────────────────────────────────
    try:
        label_map = fetch_and_sync_labels()
    except Exception as exc:
        logger.error("Label sync failed", error=str(exc))
        label_map = {}

    logger.info("Processing new messages", count=len(message_ids))

    # ── Step 5: Process each message ──────────────────────────────────────
    process_message_batch(message_ids, label_map, summary)

    logger.info(
        "Processing cycle complete",
        processed=summary["processed"],
        skipped=summary["skipped"],
        failed=summary["failed"],
        labels_created=summary["labels_created"],
        deferred=summary["deferred"],
    )
    return summary


def process_message_batch(
    message_ids: list[str],
    label_map: dict[str, str],
    summary: dict[str, int] | None = None,
) -> dict[str, int]:
    """
    Process a batch of Gmail message IDs.

    The caller has already advanced the stored historyId past these messages,
    so nothing in this batch may be silently dropped: if Groq's rate limit is
    hit, the current message *and every message after it* are pushed onto the
    retry queue and the batch stops early. Hammering the API for the rest of
    the batch would just burn the free-tier quota and fail every request.
    """
    if summary is None:
        summary = {
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "labels_created": 0,
            "deferred": 0,
        }

    for index, message_id in enumerate(message_ids):
        result = _process_single_message(message_id, label_map)

        if result == "rate_limited":
            summary["deferred"] += 1
            leftovers = message_ids[index + 1 :]
            for leftover_id in leftovers:
                _defer_rate_limited(
                    leftover_id,
                    "Deferred: Groq rate limit reached",
                    retry_after=None,
                )
            if leftovers:
                summary["deferred"] += len(leftovers)
                logger.warning(
                    "Rate limit reached — deferred the rest of the batch",
                    deferred=len(leftovers),
                )
            break

        if result == "processed":
            summary["processed"] += 1
        elif result == "skipped":
            summary["skipped"] += 1
        elif result == "failed":
            summary["failed"] += 1
        elif result == "label_created":
            summary["processed"] += 1
            summary["labels_created"] += 1

    return summary


def renew_gmail_watch() -> None:
    """Daily job to renew the Gmail Push Notification watch before it expires (7 days)."""
    logger.info("Renewing Gmail Push Notification Watch...")
    try:
        from app.gmail.messages import start_watch

        start_watch()
    except Exception as exc:
        logger.error("Failed to renew Gmail Watch", error=str(exc))


def _process_single_message(
    message_id: str,
    label_map: dict[str, str],
) -> str:
    """
    Process one message through the full pipeline.

    Returns:
        "processed"     — success
        "label_created" — success, a new Gmail label was created
        "skipped"       — already processed or excluded
        "failed"        — unrecoverable error (recorded in retries table)
        "rate_limited"  — Groq's free tier is exhausted; deferred, not lost
    """
    start = time.monotonic()

    # Guard: double-check we haven't seen this message
    with get_session() as db:
        if is_already_processed(db, message_id):
            logger.debug("Skipping already-processed message", message_id=message_id)
            return "skipped"

    # ── Download ──────────────────────────────────────────────────────────
    email_data = get_message_detail(message_id)
    if email_data is None:
        _record_failure(message_id, "Failed to download message")
        return "failed"

    logger.info(
        "Processing message",
        message_id=message_id,
        subject=email_data.get("subject", ""),
        sender=email_data.get("sender", ""),
    )

    # ── Classify ──────────────────────────────────────────────────────────
    try:
        classification = classify_email(email_data)
    except Exception as exc:
        _record_failure(message_id, f"Classification error: {exc}")
        return "failed"

    # Never label an email the model was not allowed to classify. A rate
    # limit is transient, so defer it; anything else counts as a failure.
    if not classification.get("ai_success", True):
        error = classification.get("error") or "AI classification failed"
        if classification.get("rate_limited"):
            _defer_rate_limited(message_id, error, classification.get("retry_after"))
            return "rate_limited"
        _record_failure(message_id, error)
        return "failed"

    # ── Resolve label ─────────────────────────────────────────────────────
    label_created = False
    label_name: str | None = None
    label_id: str | None = None

    target_name = (
        classification["new_label"]
        if classification["create_label"] and classification["new_label"]
        else classification["category"]
    )

    matched_name, matched_id, score = find_best_label_match(target_name, label_map)

    if matched_name and matched_id:
        label_name = matched_name
        label_id = matched_id
        logger.info(
            "Using existing label",
            label=label_name,
            score=score,
            message_id=message_id,
        )
    else:
        # Create a new Gmail label
        try:
            label_name, label_id = create_label(target_name)
            label_map[label_name] = label_id  # Update local cache
            label_created = True
            logger.info(
                "New label created",
                label=label_name,
                message_id=message_id,
            )
        except Exception as exc:
            logger.error(
                "Label creation failed",
                label=target_name,
                error=str(exc),
                message_id=message_id,
            )
            # Continue without a label — we still want to star/archive

    # ── Apply label ───────────────────────────────────────────────────────
    if label_id:
        apply_label_to_message(message_id, label_id)

    # ── Star important messages ───────────────────────────────────────────
    pass  # User requested to never star mails.

    # ── Archive low-priority messages ─────────────────────────────────────
    pass  # User requested to only add labels, not transfer/archive.

    # ── Persist to SQLite ─────────────────────────────────────────────────
    elapsed_ms = int((time.monotonic() - start) * 1000)

    with get_session() as db:
        create_processed_email(
            db,
            {
                "gmail_message_id": message_id,
                "thread_id": email_data.get("thread_id"),
                "subject": email_data.get("subject"),
                "sender": email_data.get("sender"),
                "recipient": email_data.get("recipient"),
                "received_date": email_data.get("date"),
                "category": classification["category"],
                "confidence": classification["confidence"],
                "importance": classification["importance"],
                "archived": classification["archive"],
                "starred": classification["star"],
                "label_applied": label_name,
                "gmail_label_id": label_id,
                "reason": classification["reason"],
                "processing_time_ms": elapsed_ms,
                "status": "success",
            },
        )
        # Clean up any retry record for this message
        delete_retry(db, message_id)

    return "label_created" if label_created else "processed"


def _defer_rate_limited(
    message_id: str,
    error: str,
    retry_after: float | None = None,
) -> None:
    """
    Push a message onto the retry queue because Groq's rate limit was hit.

    Deferrals do not consume the message's failure budget — the email is fine,
    the quota window just needs to reset.
    """
    delay_seconds = (
        int(retry_after) if retry_after else _settings.groq_rate_limit_max_wait
    )
    delay_seconds = max(delay_seconds, 1)

    logger.warning(
        "Rate limited — deferring message",
        message_id=message_id,
        retry_in_seconds=delay_seconds,
    )
    with get_session() as db:
        defer_rate_limited(
            db,
            message_id=message_id,
            error=error,
            delay_seconds=delay_seconds,
        )


def _record_failure(message_id: str, error: str) -> None:
    """Write a failed message to the retries table and processed_emails."""
    logger.error("Message processing failed", message_id=message_id, error=error)
    with get_session() as db:
        create_or_update_retry(
            db,
            message_id=message_id,
            error=error,
            max_retries=_settings.retry_max_attempts,
            backoff_base=_settings.retry_backoff_base,
        )
        # Only insert into processed_emails if not already there
        if not is_already_processed(db, message_id):
            create_processed_email(
                db,
                {
                    "gmail_message_id": message_id,
                    "status": "failed",
                    "reason": error,
                },
            )


# ── Retry Job ─────────────────────────────────────────────────────────────────


def retry_failed_emails() -> None:
    """
    Re-process messages that previously failed and are now due for retry.
    Deletes the existing failed-processed record so the pipeline picks
    the message up again.
    """
    with get_session() as db:
        pending = get_pending_retries(db)

    if not pending:
        return

    logger.info("Retrying failed messages", count=len(pending))

    try:
        label_map = fetch_and_sync_labels()
    except Exception:
        label_map = {}

    for index, retry_record in enumerate(pending):
        msg_id = retry_record.message_id
        logger.info(
            "Retrying message", message_id=msg_id, attempt=retry_record.retry_count
        )

        # Delete failed record so _process_single_message picks it up
        with get_session() as db:
            existing = (
                db.query(ProcessedEmail)
                .filter(ProcessedEmail.gmail_message_id == msg_id)
                .one_or_none()
            )
            if existing:
                db.delete(existing)

        result = _process_single_message(msg_id, label_map)

        if result == "rate_limited":
            # The remaining retry rows stay pending in the DB — they will be
            # picked up on the next run once the quota window has reset.
            logger.warning(
                "Rate limit reached — pausing retry queue",
                remaining=len(pending) - index - 1,
            )
            break


# ── Scheduler Lifecycle ───────────────────────────────────────────────────────


def start_scheduler() -> None:
    """Start the APScheduler background scheduler."""
    if not _settings.scheduler_enabled:
        logger.info("Scheduler disabled via config — skipping start")
        return

    scheduler = get_scheduler()

    # Main polling job — process new emails
    scheduler.add_job(
        process_new_emails,
        trigger=IntervalTrigger(seconds=_settings.poll_interval_seconds),
        id="poll_emails",
        name="Poll New Emails",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
        next_run_time=datetime.now(UTC),  # Run immediately
    )

    # Watch Renewal job — run immediately on startup then every 24 hours
    scheduler.add_job(
        renew_gmail_watch,
        trigger=IntervalTrigger(hours=24),
        id="renew_watch",
        name="Renew Gmail Watch",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
        next_run_time=datetime.now(UTC),  # Run immediately
    )

    # Retry job — run periodically to catch any failed messages
    scheduler.add_job(
        retry_failed_emails,
        trigger=IntervalTrigger(minutes=15),
        id="retry_emails",
        name="Retry Failed Emails",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started with jobs: Polling ({_settings.poll_interval_seconds}s), Watch Renewal (24h), Retry (15m)"
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")
