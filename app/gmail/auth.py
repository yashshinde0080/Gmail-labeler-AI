"""
Gmail OAuth2 authentication.

Strategy:
    - Load credentials from token.json (persisted by setup script).
    - The google-auth library handles silent token refresh automatically.
    - No refresh_token in .env — run the setup script first.

The refresh token is only stored in token.json, never in environment
variables.  This keeps secrets scoped to the local filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import get_settings
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# Module-level singleton — refreshed in place when the token expires
_credentials: Credentials | None = None


def _load_credentials_from_file() -> Credentials | None:
    """
    Load credentials from token.json.

    token.json contains the access token, refresh token, and client
    metadata.  It is created by the one-time OAuth setup script.
    Returns None if the file does not exist or is corrupt.
    """
    token_path = Path(_settings.token_path)
    if not token_path.exists():
        logger.warning("token.json not found — OAuth not set up yet")
        return None

    try:
        with token_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id", _settings.gmail_client_id),
            client_secret=data.get("client_secret", _settings.gmail_client_secret),
            scopes=data.get("scopes", _settings.scopes_list),
        )
        logger.debug("Loaded credentials from token.json")
        return creds
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning(
            "token.json corrupt — re-run the OAuth setup script",
            error=str(exc),
        )
        return None


def _save_credentials_to_file(creds: Credentials) -> None:
    """Persist the current credentials to token.json."""
    token_path = Path(_settings.token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else [],
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    with token_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    logger.debug("Credentials saved to token.json")


def get_credentials() -> Credentials:
    """
    Return valid Gmail credentials from token.json.

    Flow:
        1. Return the in-memory singleton if still valid.
        2. Load from token.json.
        3. Refresh the access token if expired.
        4. Persist the refreshed token.

    Raises RuntimeError if token.json is missing or the refresh fails.
    """
    global _credentials  # noqa: PLW0603

    # ── 1. Reuse in-memory creds if still valid ──────────────────────────
    if _credentials and _credentials.valid:
        return _credentials

    # ── 2. Load from token.json ──────────────────────────────────────────
    creds = _load_credentials_from_file()
    if creds is None:
        raise RuntimeError(
            "No Gmail credentials found.\n"
            "Run the OAuth flow manually:\n"
            "  1. Download your OAuth client JSON from Google Cloud Console\n"
            "     to data/credentials.json\n"
            "  2. Use google_auth_oauthlib to generate data/token.json"
        )

    # ── 3. Refresh if needed ─────────────────────────────────────────────
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
                    "Re-run the OAuth setup script to get a new refresh token."
                ) from exc
        else:
            raise RuntimeError(
                "Gmail credentials are invalid and cannot be refreshed.\n"
                "Re-run the OAuth setup script."
            )

    # ── 4. Persist and cache ─────────────────────────────────────────────
    _save_credentials_to_file(creds)
    _credentials = creds
    return _credentials


def invalidate_credentials() -> None:
    """Force the next call to get_credentials() to do a full refresh."""
    global _credentials  # noqa: PLW0603
    _credentials = None
    logger.info("Credentials cache invalidated — will refresh on next call")
