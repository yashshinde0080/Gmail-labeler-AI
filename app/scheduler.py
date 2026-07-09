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

from app.ai.classifier import classify_email
from app.config import get_settings
from app.database.crud import (
    create_or_update_retry,
    create_processed_email,
    delete_retry,
    get_pending_retries,
    is_already_processed,
)
from app.database.db import get_session
from app.database.models import ProcessedEmail
from app.gmail.auth import get_credentials
from app.gmail.labels import (
    apply_label_to_message,
    archive_message,
    create_label,
    fetch_and_sync_labels,
    find_best_label_match,
    star_message,
)
from app.gmail.messages import get_message_detail, list_new_message_ids
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# ── Scheduler instance ────────────────────────────────────────────────────────
_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    """Return the global scheduler, creating it if necessary."""
    global _scheduler  # noqa: PLW0603
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
    return _scheduler


# ── Main Pipeline ─────────────────────────────────────────────────────────────


def process_new_emails() -> dict[str, int]:
    """
    Full email processing pipeline:

        1. Ensure Gmail credentials are valid.
        2. Sync the label cache.
        3. List new message IDs.
        4. For each message:
            a. Download details.
            b. Classify with Groq AI.
            c. Resolve / create Gmail label.
            d. Apply label.
            e. Star or archive according to AI decision.
            f. Store result in SQLite.

    Returns a summary dict used by the /sync endpoint.
    """
    logger.info("Starting email processing cycle")
    summary = {
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "labels_created": 0,
    }

    # ── Step 1: Auth ──────────────────────────────────────────────────────
    try:
        get_credentials()
    except Exception as exc:
        logger.error("Authentication failed — aborting cycle", error=str(exc))
        return summary

    # ── Step 2: Sync labels ───────────────────────────────────────────────
    try:
        label_map = fetch_and_sync_labels()
    except Exception as exc:
        logger.error("Label sync failed", error=str(exc))
        label_map = {}

    # ── Step 3: List new messages ─────────────────────────────────────────
    try:
        message_ids = list_new_message_ids()
    except Exception as exc:
        logger.error("Failed to list messages", error=str(exc))
        return summary

    if not message_ids:
        logger.info("No new messages to process")
        return summary

    logger.info("Processing messages", count=len(message_ids))

    # ── Step 4: Process each message ──────────────────────────────────────
    for message_id in message_ids:
        result = _process_single_message(message_id, label_map)

        if result == "processed":
            summary["processed"] += 1
        elif result == "skipped":
            summary["skipped"] += 1
        elif result == "failed":
            summary["failed"] += 1
        elif result == "label_created":
            summary["processed"] += 1
            summary["labels_created"] += 1

    logger.info(
        "Processing cycle complete",
        processed=summary["processed"],
        skipped=summary["skipped"],
        failed=summary["failed"],
        labels_created=summary["labels_created"],
    )
    return summary


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
    if classification["star"] and _settings.star_high_importance:
        star_message(message_id)

    # ── Archive low-priority messages ─────────────────────────────────────
    if classification["archive"] and _settings.archive_low_importance:
        archive_message(message_id)

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

    for retry_record in pending:
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

        _process_single_message(msg_id, label_map)


# ── Scheduler Lifecycle ───────────────────────────────────────────────────────


def start_scheduler() -> None:
    """Start the APScheduler background scheduler."""
    if not _settings.scheduler_enabled:
        logger.info("Scheduler disabled via config — skipping start")
        return

    scheduler = get_scheduler()

    # Main job — run immediately on startup then every N minutes
    scheduler.add_job(
        process_new_emails,
        trigger=IntervalTrigger(minutes=_settings.poll_interval_minutes),
        id="process_emails",
        name="Process New Emails",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping runs
        misfire_grace_time=120,  # Allow up to 2 min late start
        next_run_time=datetime.now(UTC),  # Run immediately
    )

    # Retry job — offset by 2 minutes so retries happen after the main job
    scheduler.add_job(
        retry_failed_emails,
        trigger=IntervalTrigger(minutes=_settings.poll_interval_minutes),
        id="retry_emails",
        name="Retry Failed Emails",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    scheduler.start()
    logger.info(
        "Scheduler started",
        interval_minutes=_settings.poll_interval_minutes,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")
