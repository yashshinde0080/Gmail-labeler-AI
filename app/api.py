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

import base64
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
)
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


_oauth_state: dict[str, str] = {}


@router.api_route("/", methods=["GET", "HEAD"], tags=["General"])
async def root(
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Welcome endpoint — confirms the API is alive and handles Google OAuth callback."""
    if code:
        from google_auth_oauthlib.flow import Flow

        from app.database.models import OAuthToken
        from app.gmail.auth import encrypt_token

        client_config = {
            "web": {
                "client_id": _settings.gmail_client_id,
                "project_id": _settings.gmail_project_id,
                "auth_uri": _settings.gmail_auth_uri,
                "token_uri": _settings.gmail_token_uri,
                "auth_provider_x509_cert_url": _settings.gmail_cert_url,
                "client_secret": _settings.gmail_client_secret,
                "redirect_uris": [_settings.gmail_redirect_uri],
            }
        }
        flow = Flow.from_client_config(client_config, scopes=_settings.scopes_list)
        flow.redirect_uri = _settings.gmail_redirect_uri

        # Restore PKCE code verifier
        if "code_verifier" in _oauth_state:
            flow.code_verifier = _oauth_state["code_verifier"]

        flow.fetch_token(code=code)

        creds = flow.credentials
        token_record = db.query(OAuthToken).filter_by(user_id="default").first()
        if not token_record:
            token_record = OAuthToken(user_id="default")
            db.add(token_record)

        token_record.access_token_encrypted = encrypt_token(creds.token)
        if creds.refresh_token:
            token_record.refresh_token_encrypted = encrypt_token(creds.refresh_token)

        db.commit()

        # The background scheduler may hold a stale/poisoned credentials
        # object from before this login. Drop it so the very next poll uses
        # the fresh token instead of failing with invalid_grant forever.
        from app.gmail.auth import invalidate_credentials

        invalidate_credentials()

        return {
            "status": "success",
            "message": "Authenticated! Token saved to DB. You can close this window.",
        }

    return {
        "service": "Gmail AI Auto Labeler",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "auth": "/login",
    }


@router.get("/login", tags=["General"])
async def login():
    """Redirects to Google for OAuth authentication."""
    from fastapi.responses import RedirectResponse
    from google_auth_oauthlib.flow import Flow

    client_config = {
        "web": {
            "client_id": _settings.gmail_client_id,
            "project_id": _settings.gmail_project_id,
            "auth_uri": _settings.gmail_auth_uri,
            "token_uri": _settings.gmail_token_uri,
            "auth_provider_x509_cert_url": _settings.gmail_cert_url,
            "client_secret": _settings.gmail_client_secret,
            "redirect_uris": [_settings.gmail_redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=_settings.scopes_list)
    flow.redirect_uri = _settings.gmail_redirect_uri
    auth_url, state = flow.authorization_url(
        prompt="consent", access_type="offline", include_granted_scopes="true"
    )

    _oauth_state["state"] = state
    _oauth_state["code_verifier"] = flow.code_verifier

    return RedirectResponse(auth_url)


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


# ── Push Notifications (Webhooks) ──────────────────────────────────────────────


@router.post("/webhook/gmail", tags=["Gmail Push"])
async def gmail_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receives push notifications from Google Cloud Pub/Sub.
    Returns HTTP 200 immediately and processes emails in the background.
    """
    try:
        payload = await request.json()
        message = payload.get("message", {})
        data_b64 = message.get("data")
        if not data_b64:
            return {"status": "ignored", "reason": "no data"}

        data_json = base64.b64decode(data_b64).decode("utf-8")
        event = json.loads(data_json)
        history_id = event.get("historyId")

        logger.info("Webhook received", history_id=history_id)

        if not history_id:
            return {"status": "ignored", "reason": "no historyId"}

        import os

        if os.getenv("VERCEL") == "1":
            # Serverless environments freeze after HTTP response. Must process synchronously.
            logger.info("Processing webhook synchronously for Vercel")
            _process_webhook(str(history_id))
        else:
            # Process in background for persistent containers
            background_tasks.add_task(_process_webhook, str(history_id))

        return {"status": "ok"}
    except Exception as exc:
        logger.error("Webhook payload error", error=str(exc))
        return {"status": "error"}


def _process_webhook(new_history_id: str) -> None:
    """Background task to fetch and process new emails based on historyId."""
    from app.database.crud import get_setting, set_setting
    from app.database.db import get_session
    from app.gmail.labels import fetch_and_sync_labels
    from app.gmail.messages import get_new_messages_from_history
    from app.scheduler import process_message_batch

    with get_session() as db:
        last_history_id = get_setting(db, "last_history_id")

    if not last_history_id:
        logger.info("No last_history_id found; setting baseline.")
        with get_session() as db:
            set_setting(
                db, "last_history_id", new_history_id, "Latest Gmail History ID"
            )
        return

    message_ids, latest_history_id = get_new_messages_from_history(last_history_id)

    if latest_history_id:
        with get_session() as db:
            set_setting(db, "last_history_id", str(latest_history_id))

    if not message_ids:
        logger.info("No new messages from webhook")
        return

    logger.info("Processing messages from webhook", count=len(message_ids))
    try:
        label_map = fetch_and_sync_labels()
    except Exception:
        label_map = {}

    # Defers the rest of the batch automatically if Groq's rate limit is hit.
    process_message_batch(message_ids, label_map)


@router.get("/watch/status", tags=["Gmail Push"])
async def watch_status(db: Session = Depends(get_db_session)):
    """Check the status of the Gmail Push Notification watch, or start it."""
    from app.database.crud import set_setting
    from app.gmail.messages import start_watch

    try:
        response = start_watch()
        history_id = response.get("historyId")
        if history_id:
            set_setting(
                db, "last_history_id", str(history_id), "Latest Gmail History ID"
            )
        return {"status": "success", "watch_response": response}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router.get("/gmail/status", tags=["Gmail Push"])
async def gmail_status(db: Session = Depends(get_db_session)):
    """Get the current sync state."""
    from app.database.crud import get_setting

    last_history_id = get_setting(db, "last_history_id")
    return {
        "pubsub_topic": _settings.gcp_pubsub_topic,
        "last_history_id": last_history_id,
        "push_notifications_enabled": bool(last_history_id),
    }


# ── Vercel & Control ──────────────────────────────────────────────────────────


def verify_vercel_cron(authorization: str | None = Header(None)):
    """Optional security check for Vercel Cron invocations."""
    cron_secret = _settings.vercel_cron_secret
    if cron_secret and authorization != f"Bearer {cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized Cron invocation")


@router.get("/api/cron/renew_watch", tags=["Vercel Cron"])
async def cron_renew_watch(auth: None = Depends(verify_vercel_cron)):
    """Vercel Serverless Cron endpoint to renew Gmail Watch."""
    from app.scheduler import renew_gmail_watch

    renew_gmail_watch()
    return {"status": "success"}


@router.get("/api/cron/retry", tags=["Vercel Cron"])
async def cron_retry_failed(auth: None = Depends(verify_vercel_cron)):
    """Vercel Serverless Cron endpoint to retry failed emails."""
    from app.scheduler import retry_failed_emails

    retry_failed_emails()
    return {"status": "success"}


@router.get("/api/cron/process", tags=["Vercel Cron"])
@router.post("/api/cron/process", tags=["Vercel Cron"])
async def cron_process_emails(auth: None = Depends(verify_vercel_cron)):
    """
    Cron endpoint for processing new emails.

    Exposed as both GET and POST: Vercel Cron Jobs always issue GET requests,
    while POST stays available for manual invocations.
    """
    from app.scheduler import process_new_emails

    summary = process_new_emails()
    return {"status": "success", "summary": summary}


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
