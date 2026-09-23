"""
Unit tests for the AI classifier.

These tests mock the Groq API so they run without credentials and
do not consume any free-tier quota.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.ai.classifier import _parse_json, _validate_and_sanitise, classify_email
from app.ai.groq_client import GroqRateLimitedError

# ── _parse_json ───────────────────────────────────────────────────────────────


class TestParseJson:
    def test_valid_json(self):
        raw = '{"category": "Work", "confidence": 90}'
        result = _parse_json(raw, "msg1")
        assert result == {"category": "Work", "confidence": 90}

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"category": "Finance"}\n```'
        result = _parse_json(raw, "msg1")
        assert result["category"] == "Finance"

    def test_json_embedded_in_prose(self):
        raw = 'Here is the result: {"category": "GitHub", "confidence": 85} Done.'
        result = _parse_json(raw, "msg1")
        assert result["category"] == "GitHub"

    def test_invalid_json_returns_none(self):
        result = _parse_json("this is not json at all", "msg1")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_json("", "msg1")
        assert result is None


# ── _validate_and_sanitise ────────────────────────────────────────────────────


class TestValidateAndSanitise:
    def _valid_input(self, **overrides):
        base = {
            "category": "Work",
            "confidence": 92,
            "importance": "high",
            "archive": False,
            "star": True,
            "create_label": False,
            "new_label": "",
            "reason": "A work email",
        }
        base.update(overrides)
        return base

    def test_happy_path(self):
        result = _validate_and_sanitise(self._valid_input(), "msg1")
        assert result["category"] == "Work"
        assert result["confidence"] == 92
        assert result["importance"] == "high"
        assert result["star"] is True
        assert result["archive"] is False

    def test_confidence_clamped_to_100(self):
        result = _validate_and_sanitise(self._valid_input(confidence=150), "msg1")
        assert result["confidence"] == 100

    def test_confidence_clamped_to_0(self):
        result = _validate_and_sanitise(self._valid_input(confidence=-10), "msg1")
        assert result["confidence"] == 0

    def test_invalid_importance_defaults_to_low(self):
        result = _validate_and_sanitise(
            self._valid_input(importance="CRITICAL"), "msg1"
        )
        assert result["importance"] == "low"

    def test_new_label_cleared_when_create_label_false(self):
        result = _validate_and_sanitise(
            self._valid_input(create_label=False, new_label="SomeLabel"), "msg1"
        )
        assert result["new_label"] == ""

    def test_dangerous_chars_stripped_from_category(self):
        result = _validate_and_sanitise(
            self._valid_input(category="Work<script>alert(1)</script>"), "msg1"
        )
        assert "<" not in result["category"]
        assert ">" not in result["category"]

    def test_missing_fields_use_defaults(self):
        result = _validate_and_sanitise({}, "msg1")
        assert result["category"] == "Uncategorised"
        assert result["confidence"] == 0
        assert result["importance"] == "low"


# ── classify_email ────────────────────────────────────────────────────────────


class TestClassifyEmail:
    """Integration-style tests with Groq mocked out."""

    _sample_email = {
        "message_id": "test_msg_001",
        "thread_id": "thread_001",
        "subject": "Your invoice is ready",
        "sender": "billing@example.com",
        "recipient": "user@gmail.com",
        "date": "Mon, 01 Jan 2024 10:00:00 +0000",
        "cleaned_text": "Please find your invoice attached. Total due: $99.",
        "attachments": ["invoice_001.pdf"],
        "snippet": "Invoice attached.",
    }

    _mock_groq_response = {
        "content": '{"category":"Finance","confidence":95,"importance":"high",'
        '"archive":false,"star":true,"create_label":false,'
        '"new_label":"","reason":"Invoice email"}',
        "model": "llama-3.1-8b-instant",
        "prompt_tokens": 300,
        "completion_tokens": 50,
        "total_tokens": 350,
        "response_time_ms": 800,
    }

    @patch("app.ai.classifier.call_groq")
    @patch("app.ai.classifier.create_ai_log")
    @patch("app.ai.classifier.get_session")
    def test_successful_classification(self, mock_session, mock_log, mock_groq):
        mock_groq.return_value = self._mock_groq_response
        mock_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session.return_value.__exit__ = MagicMock(return_value=False)

        result = classify_email(self._sample_email)

        assert result["category"] == "Finance"
        assert result["confidence"] == 95
        assert result["importance"] == "high"
        assert result["star"] is True
        assert result["ai_success"] is True
        assert result["rate_limited"] is False

    @patch("app.ai.classifier.call_groq")
    @patch("app.ai.classifier.create_ai_log")
    @patch("app.ai.classifier.get_session")
    def test_groq_failure_returns_fallback(self, mock_session, mock_log, mock_groq):
        mock_groq.side_effect = Exception("Network error")
        mock_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session.return_value.__exit__ = MagicMock(return_value=False)

        result = classify_email(self._sample_email)

        # Should return the fallback, not raise
        assert result["category"] == "Uncategorised"
        assert result["confidence"] == 0
        assert result["ai_success"] is False
        assert result["rate_limited"] is False

    @patch("app.ai.classifier.call_groq")
    @patch("app.ai.classifier.create_ai_log")
    @patch("app.ai.classifier.get_session")
    def test_rate_limit_is_flagged_for_deferral(
        self, mock_session, mock_log, mock_groq
    ):
        mock_groq.side_effect = GroqRateLimitedError("slow down", retry_after=42)
        mock_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session.return_value.__exit__ = MagicMock(return_value=False)

        result = classify_email(self._sample_email)

        # A rate limit must not look like a real "Uncategorised" verdict.
        assert result["rate_limited"] is True
        assert result["ai_success"] is False
        assert result["retry_after"] == 42
