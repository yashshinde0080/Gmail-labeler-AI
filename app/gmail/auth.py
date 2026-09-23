"""
Gmail OAuth2 authentication.

Builds Credentials dynamically and uses encrypted DB storage.
"""

from __future__ import annotations

from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import get_settings
from app.database.db import get_session
from app.database.models import OAuthToken
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

_fernet = (
    Fernet(_settings.fernet_key.encode())
    if hasattr(_settings, "fernet_key") and _settings.fernet_key
    else None
)
_credentials: Credentials | None = None


def encrypt_token(token: str | None) -> bytes | None:
    if not token or not _fernet:
        return None
    return _fernet.encrypt(token.encode())


def decrypt_token(token_bytes: bytes | None) -> str | None:
    if not token_bytes or not _fernet:
        return None
    return _fernet.decrypt(token_bytes).decode()


def get_credentials() -> Credentials:
    """
    Return valid Gmail credentials from the database.
    """
    global _credentials  # noqa: PLW0603

    if _credentials and _credentials.valid:
        return _credentials

    with get_session() as db:
        token_record = db.query(OAuthToken).filter_by(user_id="default").first()

    if not token_record:
        raise RuntimeError("Token missing. Visit /login to authenticate.")

    creds = Credentials(
        token=decrypt_token(token_record.access_token_encrypted),
        refresh_token=decrypt_token(token_record.refresh_token_encrypted),
        token_uri=_settings.gmail_token_uri,
        client_id=_settings.gmail_client_id,
        client_secret=_settings.gmail_client_secret,
        scopes=_settings.scopes_list,
    )

    if not creds.valid:
        try:
            if creds.expired and creds.refresh_token:
                logger.info("Refreshing Gmail access token")
                creds.refresh(Request())
                logger.info("Gmail token refreshed successfully")

                # Save new access token
                with get_session() as db:
                    tr = db.query(OAuthToken).filter_by(user_id="default").first()
                    if tr:
                        tr.access_token_encrypted = encrypt_token(creds.token)
                        if creds.refresh_token:
                            tr.refresh_token_encrypted = encrypt_token(
                                creds.refresh_token
                            )
            else:
                raise RuntimeError("Token missing. Visit /login to authenticate.")
        except RefreshError as exc:
            # invalid_grant is permanent: the refresh token was revoked or has
            # expired (Google also expires them for apps in "Testing" mode).
            # Delete the poisoned record so every cycle stops attempting a
            # doomed refresh, and require a fresh /login.
            _credentials = None
            with get_session() as db:
                poisoned = db.query(OAuthToken).filter_by(user_id="default").first()
                if poisoned:
                    db.delete(poisoned)
            logger.error(
                "Gmail refresh token rejected (invalid_grant) — "
                "visit /login to re-authenticate",
                error=str(exc),
            )
            raise RuntimeError(
                f"Gmail refresh token was rejected: {exc}\n"
                f"Visit /login to re-authenticate."
            ) from exc
        except Exception as exc:
            logger.error("Authentication failed", error=str(exc))
            raise RuntimeError(
                f"Gmail auth failed: {exc}\nVisit /login to authenticate."
            ) from exc

    _credentials = creds
    return _credentials


def invalidate_credentials() -> None:
    """Force the next call to get_credentials() to do a full refresh."""
    global _credentials  # noqa: PLW0603
    _credentials = None
    logger.info("Credentials cache invalidated")
