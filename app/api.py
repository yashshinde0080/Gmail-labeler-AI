"""
FastAPI REST API.

Endpoints:
    GET  /              — welcome message
    GET  /health        — liveness probe for Render / load balancers
    GET  /stats         — email processing statistics
    GET  /labels        — all known Gmail labels
    GET  /logs          — recent processed emails (paginated)
    POST /sync          — manually trigger a processing cycle
    POST /reprocess     — queue a specific message for reprocessing
    GET  /metrics       — AI token usage metrics
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.crud import (
    get_ai_metrics,
    get_all_labels,
    get_processed_email,
    get_processing_stats,
    list_processed_emails,
)
from app.database.db import get_db_session
from app.logger import get_logger
from app.scheduler import process_new_emails

logger = get_logger(__name__)
_settings = get_settings()

router = APIRouter()

_startup_time = datetime.now(UTC)


# ── Pydantic response schemas ─────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: int
    timestamp: str


class StatsResponse(BaseModel):
    total: int
    success: int
    failed: int
    archived: int
    starred: int
    avg_confidence: float
    top_categories: list[dict[str, Any]]


class LabelItem(BaseModel):
    id: int
    gmail_label_id: str
    label_name: str
    created_by_us: bool
    created_at: str


class EmailLogItem(BaseModel):
    id: int
    gmail_message_id: str
    subject: str | None
    sender: str | None
    category: str | None
    confidence: int | None
    importance: str | None
    archived: bool
    starred: bool
    label_applied: str | None
    reason: str | None
    processed_at: str
    status: str


class SyncResponse(BaseModel):
    triggered: bool
    message: str


class ReprocessRequest(BaseModel):
    message_id: str


class MetricsResponse(BaseModel):
    total_calls: int
    success_calls: int
    failed_calls: int
    total_tokens_used: int
    avg_response_time_ms: float


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/", tags=["General"])
async def root() -> dict[str, str]:
    """Welcome endpoint — confirms the API is alive."""
    return {
        "service": "Gmail AI Auto Labeler",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@router.get("/health", response_model=HealthResponse, tags=["General"])
async def health_check() -> HealthResponse:
    """
    Liveness probe used by Render, Docker health checks, and uptime monitors.
    Returns HTTP 200 when the service is healthy.
    """
    uptime = int((datetime.now(UTC) - _startup_time).total_seconds())
    return HealthResponse(
        status="healthy",
        uptime_seconds=uptime,
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get("/stats", response_model=StatsResponse, tags=["Analytics"])
async def get_stats(db: Session = Depends(get_db_session)) -> StatsResponse:
    """
    Processing statistics: totals, categories, average confidence.
    """
    stats = get_processing_stats(db)
    return StatsResponse(**stats)


@router.get("/labels", response_model=list[LabelItem], tags=["Labels"])
async def list_labels(db: Session = Depends(get_db_session)) -> list[LabelItem]:
    """All Gmail labels known to the system (from local cache)."""
    labels = get_all_labels(db)
    return [
        LabelItem(
            id=lbl.id,
            gmail_label_id=lbl.gmail_label_id,
            label_name=lbl.label_name,
            created_by_us=lbl.created_by_us,
            created_at=lbl.created_at.isoformat(),
        )
        for lbl in labels
    ]


@router.get("/logs", response_model=list[EmailLogItem], tags=["Logs"])
async def get_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db_session),
) -> list[EmailLogItem]:
    """
    Paginated list of recently processed emails, newest first.
    Use ?limit=100&offset=0 for the first page.
    """
    emails = list_processed_emails(db, limit=limit, offset=offset)
    return [
        EmailLogItem(
            id=e.id,
            gmail_message_id=e.gmail_message_id,
            subject=e.subject,
            sender=e.sender,
            category=e.category,
            confidence=e.confidence,
            importance=e.importance,
            archived=e.archived,
            starred=e.starred,
            label_applied=e.label_applied,
            reason=e.reason,
            processed_at=e.processed_at.isoformat(),
            status=e.status,
        )
        for e in emails
    ]


@router.post("/sync", response_model=SyncResponse, tags=["Control"])
async def trigger_sync(background_tasks: BackgroundTasks) -> SyncResponse:
    """
    Manually trigger an email processing cycle without waiting for the
    next scheduled run.  The cycle runs in a background task so the
    HTTP response returns immediately.
    """
    logger.info("Manual sync triggered via API")
    background_tasks.add_task(process_new_emails)
    return SyncResponse(
        triggered=True,
        message="Email processing cycle started in background",
    )


@router.post("/reprocess", tags=["Control"])
async def reprocess_email(
    request: ReprocessRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Queue a specific Gmail message ID for reprocessing.

    Use this when you want the AI to re-classify an email that was
    previously processed (e.g. after updating the prompt or model).
    """
    msg_id = request.message_id

    existing = get_processed_email(db, msg_id)
    if not existing:
        raise HTTPException(
            status_code=404,
            detail=f"Message {msg_id!r} not found in processed emails",
        )

    logger.info("Reprocessing requested", message_id=msg_id)

    # We delete the existing record so the pipeline picks it up again
    db.delete(existing)
    db.commit()

    from app.gmail.labels import fetch_and_sync_labels
    from app.scheduler import _process_single_message

    async def _reprocess():
        label_map = fetch_and_sync_labels()
        _process_single_message(msg_id, label_map)

    background_tasks.add_task(_reprocess)

    return {
        "message_id": msg_id,
        "status": "queued_for_reprocessing",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/metrics", response_model=MetricsResponse, tags=["Analytics"])
async def get_metrics(db: Session = Depends(get_db_session)) -> MetricsResponse:
    """
    AI token usage and response-time metrics.
    Use this to track Groq free-tier consumption.
    """
    metrics = get_ai_metrics(db)
    return MetricsResponse(**metrics)
