"""
Unit tests for the credential lifecycle.

These cover the deployment failure mode from the Render logs: a revoked
Gmail refresh token kept failing with ``invalid_grant`` even after the user
re-authenticated, because stale credential objects were cached in memory
and the OAuth callback never invalidated them.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.config import Settings, get_settings


def _http_error(status: int) -> HttpError:
    resp = httplib2.Response({"status": status})
    return HttpError(resp, b"{}")


class TestDatabaseUrlRedaction:
    def _settings_with(self, url: str) -> Settings:
        return Settings(
            groq_api_key="k",
            gmail_client_id="id",
            gmail_client_secret="secret",
            fernet_key=get_settings().fernet_key,
            database_url=url,
        )

    def test_masks_password_in_postgres_url(self):
        url = (
            "postgresql://neondb_owner:npg_SuperSecret@ep-host.neon.tech/"
            "neondb?sslmode=require&channel_binding=require"
        )
        redacted = self._settings_with(url).database_url_redacted
        assert "npg_SuperSecret" not in redacted
        assert redacted.startswith("postgresql://neondb_owner:***@")
        assert "ep-host.neon.tech/neondb" in redacted

    def test_sqlite_url_without_password_is_unchanged(self):
        url = "sqlite:///./data/gmail.db"
        assert self._settings_with(url).database_url_redacted == url


class TestInvalidateCredentials:
    def test_clears_cached_credentials(self):
        from app.gmail import auth as auth_module

        auth_module._credentials = MagicMock()
        auth_module.invalidate_credentials()
        assert auth_module._credentials is None


class TestRevokedRefreshToken:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        from app.gmail import auth as auth_module

        auth_module._credentials = None
        yield
        auth_module._credentials = None

    def test_invalid_grant_deletes_poisoned_token(self):
        from app.database.db import get_session
        from app.database.models import OAuthToken
        from app.gmail import auth as auth_module
        from app.gmail.auth import encrypt_token, get_credentials

        with get_session() as db:
            db.add(
                OAuthToken(
                    user_id="default",
                    access_token_encrypted=encrypt_token("stale-access"),
                    refresh_token_encrypted=encrypt_token("stale-refresh"),
                )
            )

        rejected = MagicMock()
        rejected.valid = False
        rejected.expired = True
        rejected.refresh_token = "rt"
        rejected.refresh.side_effect = RefreshError("invalid_grant")

        with patch.object(auth_module, "Credentials", return_value=rejected):
            with pytest.raises(RuntimeError, match="invalid_grant|/login"):
                get_credentials()

        # The doomed token row must be gone, so nothing retries it in a loop.
        with get_session() as db:
            assert db.query(OAuthToken).filter_by(user_id="default").first() is None


class TestSchedulerSelfHealing:
    @patch("app.gmail.auth.invalidate_credentials")
    @patch("app.gmail.service.get_credentials")
    @patch("app.gmail.messages.get_new_messages_from_history")
    @patch("app.scheduler.get_credentials")
    def test_401_clears_credential_cache(
        self, mock_creds, mock_history, mock_service_creds, mock_invalidate
    ):
        from app.scheduler import process_new_emails

        mock_creds.return_value = MagicMock()
        mock_service_creds.return_value = MagicMock()
        mock_history.side_effect = _http_error(401)

        with patch("app.database.crud.get_setting", return_value="12345"):
            summary = process_new_emails()

        # The cache must be dropped so the next cycle rebuilds credentials
        # from the DB instead of failing identically forever.
        mock_invalidate.assert_called_once()
        assert summary["processed"] == 0

    @patch("app.gmail.auth.invalidate_credentials")
    @patch("app.gmail.service.get_credentials")
    @patch("app.gmail.messages.get_new_messages_from_history")
    @patch("app.scheduler.get_credentials")
    def test_other_http_errors_do_not_touch_cache(
        self, mock_creds, mock_history, mock_service_creds, mock_invalidate
    ):
        from app.scheduler import process_new_emails

        mock_creds.return_value = MagicMock()
        mock_service_creds.return_value = MagicMock()
        mock_history.side_effect = _http_error(500)

        with patch("app.database.crud.get_setting", return_value="12345"):
            process_new_emails()

        mock_invalidate.assert_not_called()
