"""
Unit tests for the batch pipeline's rate-limit behaviour.

The Gmail and Groq calls are mocked out, so these tests only exercise the
scheduling logic: when the free tier is exhausted no message may be lost,
and the API must stop being hammered for the rest of the batch.
"""

from __future__ import annotations

from unittest.mock import patch

from app.database.crud import (
    defer_rate_limited,
    get_pending_retries,
    is_already_processed,
)
from app.database.db import get_session
from app.database.models import Retry
from app.scheduler import _process_single_message, process_message_batch


def _empty_summary() -> dict[str, int]:
    return {
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "labels_created": 0,
        "deferred": 0,
    }


class TestProcessMessageBatch:
    @patch("app.scheduler._defer_rate_limited")
    @patch("app.scheduler._process_single_message")
    def test_defers_every_remaining_message(self, mock_process, mock_defer):
        # The historyId has already advanced. "b" defers itself inside
        # _process_single_message; the batch must queue c and d.
        mock_process.side_effect = ["processed", "rate_limited"]
        summary = _empty_summary()

        result = process_message_batch(["a", "b", "c", "d"], {}, summary)

        assert result["processed"] == 1
        assert result["deferred"] == 3  # b (self-deferred) + c + d
        assert [call.args[0] for call in mock_defer.call_args_list] == ["c", "d"]

    @patch("app.scheduler._defer_rate_limited")
    @patch("app.scheduler._process_single_message")
    def test_stops_calling_groq_after_rate_limit(self, mock_process, mock_defer):
        mock_process.return_value = "rate_limited"

        result = process_message_batch(["a", "b", "c"], {})

        assert mock_process.call_count == 1
        assert result["deferred"] == 3

    @patch("app.scheduler._defer_rate_limited")
    @patch("app.scheduler._process_single_message")
    def test_counts_each_outcome(self, mock_process, mock_defer):
        mock_process.side_effect = [
            "processed",
            "label_created",
            "skipped",
            "failed",
        ]

        result = process_message_batch(["a", "b", "c", "d"], {})

        assert result == {
            "processed": 2,  # processed + label_created
            "skipped": 1,
            "failed": 1,
            "labels_created": 1,
            "deferred": 0,
        }
        mock_defer.assert_not_called()


class TestProcessSingleMessageRateLimit:
    @patch("app.scheduler.classify_email")
    @patch("app.scheduler.get_message_detail")
    def test_rate_limited_message_is_deferred_not_labelled(
        self, mock_detail, mock_classify
    ):
        mock_detail.return_value = {"message_id": "msg_e2e_1", "subject": "hi"}
        mock_classify.return_value = {
            "category": "Uncategorised",
            "confidence": 0,
            "importance": "low",
            "archive": False,
            "star": False,
            "create_label": True,
            "new_label": "Uncategorised",
            "reason": "rate limited",
            "ai_success": False,
            "rate_limited": True,
            "error": "429",
            "retry_after": 30,
        }

        assert _process_single_message("msg_e2e_1", {}) == "rate_limited"

        with get_session() as db:
            # Never written to processed_emails — otherwise the message would
            # be skipped forever with a bogus "Uncategorised" label.
            assert not is_already_processed(db, "msg_e2e_1")
            # Queued for a later attempt instead.
            row = db.query(Retry).filter_by(message_id="msg_e2e_1").one()
            assert row.status == "pending"


class TestDeferRateLimited:
    """A quota window resetting must not burn a message's retry budget."""

    def test_queues_a_pending_retry(self):
        with get_session() as db:
            defer_rate_limited(db, "msg_defer_a", "429", delay_seconds=0)

        with get_session() as db:
            row = db.query(Retry).filter_by(message_id="msg_defer_a").one()
            assert row.status == "pending"
            assert row.retry_count == 0
            assert row.next_retry_at is not None
            # Due immediately, so the next retry run picks it up.
            assert any(r.message_id == "msg_defer_a" for r in get_pending_retries(db))

    def test_repeated_deferrals_never_abandon_the_message(self):
        with get_session() as db:
            for _ in range(6):
                defer_rate_limited(db, "msg_defer_b", "429", delay_seconds=0)

        with get_session() as db:
            row = db.query(Retry).filter_by(message_id="msg_defer_b").one()
            assert row.retry_count == 0
            assert row.status == "pending"

    def test_revives_an_abandoned_message(self):
        with get_session() as db:
            db.add(Retry(message_id="msg_defer_c", retry_count=5, status="abandoned"))

        with get_session() as db:
            defer_rate_limited(db, "msg_defer_c", "429", delay_seconds=30)

        with get_session() as db:
            row = db.query(Retry).filter_by(message_id="msg_defer_c").one()
            assert row.status == "pending"
            assert row.last_error == "429"
