"""
Gmail OAuth2 authentication.

Builds Credentials directly from .env variables — no files needed.
The google-auth library handles silent token refresh automatically.
"""

from __future__ import annotations

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import get_settings
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# Module-level singleton — refreshed in place when the token expires
_credentials: Credentials | None = None


def _build_credentials() -> Credentials:
    """Build Credentials from .env variables."""
    return Credentials(
        token=None,
        refresh_token=_settings.gmail_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_settings.gmail_client_id,
        client_secret=_settings.gmail_client_secret,
        scopes=_settings.scopes_list,
    )


def get_credentials() -> Credentials:
    """
    Return valid Gmail credentials, refreshing if necessary.

    Flow:
        1. Return the in-memory singleton if still valid.
        2. Build fresh Credentials from .env.
        3. Refresh the access token if expired.

    Raises RuntimeError if refresh fails.
    """
    global _credentials  # noqa: PLW0603

    # ── 1. Reuse cached creds if still valid ──────────────────────────────
    if _credentials and _credentials.valid:
        return _credentials

    # ── 2. Build from .env ────────────────────────────────────────────────
    creds = _build_credentials()

    # ── 3. Refresh to get an access token ─────────────────────────────────
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing Gmail access token")
                creds.refresh(Request())
                logger.info("Gmail token refreshed successfully")
            except RefreshError as exc:
                logger.error("Failed to refresh Gmail token", error=str(exc))
                raise RuntimeError(
                    f"Gmail token refresh failed: {exc}\n"
                    "Check that GMAIL_REFRESH_TOKEN in .env is valid."
                ) from exc
        else:
            logger.info("Obtaining initial Gmail access token")
            creds.refresh(Request())
            logger.info("Gmail access token obtained")

    _credentials = creds
    return _credentials


def invalidate_credentials() -> None:
    """Force the next call to get_credentials() to do a full refresh."""
    global _credentials  # noqa: PLW0603
    _credentials = None
    logger.info("Credentials cache invalidated — will refresh on next call")
