"""
Unit tests for Gmail history sync.

These mock the Gmail service so no credentials are needed. The key case is
an expired history ID: the stored baseline must be replaced, otherwise the
pipeline would keep failing the same way and silently stop labelling mail.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.gmail.messages import get_new_messages_from_history


def _http_error(status: int) -> HttpError:
    resp = httplib2.Response({"status": status})
    return HttpError(resp, b"{}")


def _execute_mock(mock_service: MagicMock) -> MagicMock:
    return mock_service.return_value.users().history().list().execute


class TestHistorySync:
    @patch("app.gmail.messages.get_current_history_id", return_value="fresh-baseline")
    @patch("app.gmail.messages.get_gmail_service")
    def test_expired_history_id_returns_fresh_baseline(
        self, mock_service, mock_current
    ):
        _execute_mock(mock_service).side_effect = _http_error(404)

        message_ids, latest = get_new_messages_from_history("stale-id")

        assert message_ids == []
        # A baseline must come back so the caller can store it; returning
        # None would leave the expired ID in place forever.
        assert latest == "fresh-baseline"
        mock_current.assert_called_once()

    @patch("app.gmail.messages.get_current_history_id")
    @patch("app.gmail.messages.get_gmail_service")
    def test_other_http_errors_propagate(self, mock_service, mock_current):
        _execute_mock(mock_service).side_effect = _http_error(403)

        with pytest.raises(HttpError):
            get_new_messages_from_history("some-id")

        mock_current.assert_not_called()

    @patch("app.gmail.messages.get_gmail_service")
    def test_returns_deduplicated_message_ids(self, mock_service):
        _execute_mock(mock_service).return_value = {
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "m1"}},
                        {"message": {"id": "m2"}},
                    ]
                },
                {"messagesAdded": [{"message": {"id": "m1"}}]},  # duplicate
            ],
            "historyId": "12345",
        }

        message_ids, latest = get_new_messages_from_history("100")

        assert sorted(message_ids) == ["m1", "m2"]
        assert latest == "12345"
