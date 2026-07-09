"""
CRUD helpers — thin wrappers around raw SQLAlchemy queries.

All functions accept an explicit Session so they can participate in
caller-managed transactions, which keeps the business logic clean.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import AILog, AppSetting, Label, ProcessedEmail, Retry
from app.logger import get_logger

logger = get_logger(__name__)


# ── ProcessedEmail ────────────────────────────────────────────────────────────


def is_already_processed(db: Session, gmail_message_id: str) -> bool:
    """Return True if this Gmail message ID exists in processed_emails."""
    stmt = select(ProcessedEmail.id).where(
        ProcessedEmail.gmail_message_id == gmail_message_id
    )
    return db.execute(stmt).first() is not None


def create_processed_email(db: Session, data: dict[str, Any]) -> ProcessedEmail:
    """Insert a new processed-email record and return it."""
    record = ProcessedEmail(**data)
    db.add(record)
    db.flush()  # Populate .id without committing
    logger.debug(
        "Inserted processed_email",
        id=record.id,
        message_id=record.gmail_message_id,
    )
    return record


def get_processed_email(db: Session, gmail_message_id: str) -> ProcessedEmail | None:
    """Fetch a single processed-email by its Gmail message ID."""
    return db.execute(
        select(ProcessedEmail).where(
            ProcessedEmail.gmail_message_id == gmail_message_id
        )
    ).scalar_one_or_none()


def list_processed_emails(
    db: Session, limit: int = 100, offset: int = 0
) -> list[ProcessedEmail]:
    """Return a paginated list of processed emails, newest first."""
    stmt = (
        select(ProcessedEmail)
        .order_by(ProcessedEmail.processed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(stmt).scalars().all())


def count_processed_emails(db: Session) -> int:
    return db.execute(select(func.count()).select_from(ProcessedEmail)).scalar_one()


def get_processing_stats(db: Session) -> dict[str, Any]:
    """Aggregate statistics used by the /stats endpoint."""
    total = db.execute(select(func.count()).select_from(ProcessedEmail)).scalar_one()

    success = db.execute(
        select(func.count())
        .select_from(ProcessedEmail)
        .where(ProcessedEmail.status == "success")
    ).scalar_one()

    failed = db.execute(
        select(func.count())
        .select_from(ProcessedEmail)
        .where(ProcessedEmail.status == "failed")
    ).scalar_one()

    archived = db.execute(
        select(func.count())
        .select_from(ProcessedEmail)
        .where(
            ProcessedEmail.archived == True  # noqa: E712
        )
    ).scalar_one()

    starred = db.execute(
        select(func.count())
        .select_from(ProcessedEmail)
        .where(
            ProcessedEmail.starred == True  # noqa: E712
        )
    ).scalar_one()

    avg_confidence = db.execute(
        select(func.avg(ProcessedEmail.confidence)).select_from(ProcessedEmail)
    ).scalar_one()

    # Top 10 categories by email count
    category_rows = db.execute(
        select(ProcessedEmail.category, func.count().label("cnt"))
        .group_by(ProcessedEmail.category)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "archived": archived,
        "starred": starred,
        "avg_confidence": round(avg_confidence or 0, 1),
        "top_categories": [
            {"category": r.category, "count": r.cnt} for r in category_rows
        ],
    }


# ── Labels ────────────────────────────────────────────────────────────────────


def upsert_label(
    db: Session,
    gmail_label_id: str,
    label_name: str,
    created_by_us: bool = False,
) -> Label:
    """Insert a label if it does not exist; update its name otherwise."""
    existing = db.execute(
        select(Label).where(Label.gmail_label_id == gmail_label_id)
    ).scalar_one_or_none()

    if existing:
        existing.label_name = label_name
        db.flush()
        return existing

    label = Label(
        gmail_label_id=gmail_label_id,
        label_name=label_name,
        created_by_us=created_by_us,
    )
    db.add(label)
    db.flush()
    return label


def get_all_labels(db: Session) -> list[Label]:
    return list(db.execute(select(Label).order_by(Label.label_name)).scalars().all())


def find_label_by_name(db: Session, name: str) -> Label | None:
    return db.execute(
        select(Label).where(func.lower(Label.label_name) == name.lower())
    ).scalar_one_or_none()


# ── AI Logs ───────────────────────────────────────────────────────────────────


def create_ai_log(db: Session, data: dict[str, Any]) -> AILog:
    """Record a Groq API call result."""
    log = AILog(**data)
    db.add(log)
    db.flush()
    return log


def get_ai_metrics(db: Session) -> dict[str, Any]:
    """Token consumption and latency summary for /metrics."""
    total_calls = db.execute(select(func.count()).select_from(AILog)).scalar_one()

    total_tokens = (
        db.execute(select(func.sum(AILog.total_tokens)).select_from(AILog)).scalar_one()
        or 0
    )

    avg_latency = (
        db.execute(
            select(func.avg(AILog.response_time_ms)).select_from(AILog)
        ).scalar_one()
        or 0
    )

    success_count = db.execute(
        select(func.count()).select_from(AILog).where(AILog.success == True)  # noqa: E712
    ).scalar_one()

    return {
        "total_calls": total_calls,
        "success_calls": success_count,
        "failed_calls": total_calls - success_count,
        "total_tokens_used": total_tokens,
        "avg_response_time_ms": round(avg_latency, 1),
    }


# ── Retries ───────────────────────────────────────────────────────────────────


def create_or_update_retry(
    db: Session,
    message_id: str,
    error: str,
    max_retries: int,
    backoff_base: int,
) -> Retry:
    """
    Create a retry record for a failing message, or increment its counter.
    Sets next_retry_at using exponential back-off.
    """
    existing = db.execute(
        select(Retry).where(Retry.message_id == message_id)
    ).scalar_one_or_none()

    if existing:
        existing.retry_count += 1
        existing.last_error = error
        delay_minutes = backoff_base**existing.retry_count
        existing.next_retry_at = datetime.now(UTC) + timedelta(minutes=delay_minutes)
        if existing.retry_count >= max_retries:
            existing.status = "abandoned"
            logger.warning(
                "Message abandoned after max retries",
                message_id=message_id,
                retries=existing.retry_count,
            )
        db.flush()
        return existing

    retry = Retry(
        message_id=message_id,
        retry_count=1,
        last_error=error,
        next_retry_at=datetime.now(UTC) + timedelta(minutes=backoff_base),
        status="pending",
    )
    db.add(retry)
    db.flush()
    return retry


def get_pending_retries(db: Session) -> list[Retry]:
    """Return all retry records that are due and not yet abandoned."""
    now = datetime.now(UTC)
    return list(
        db.execute(
            select(Retry).where(
                Retry.status == "pending",
                Retry.next_retry_at <= now,
            )
        )
        .scalars()
        .all()
    )


def delete_retry(db: Session, message_id: str) -> None:
    """Remove a retry record after successful reprocessing."""
    existing = db.execute(
        select(Retry).where(Retry.message_id == message_id)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()


# ── App Settings ──────────────────────────────────────────────────────────────


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.execute(
        select(AppSetting).where(AppSetting.key == key)
    ).scalar_one_or_none()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str, description: str = "") -> None:
    existing = db.execute(
        select(AppSetting).where(AppSetting.key == key)
    ).scalar_one_or_none()
    if existing:
        existing.value = value
        existing.updated_at = datetime.now(UTC)
    else:
        db.add(AppSetting(key=key, value=value, description=description))
    db.flush()
