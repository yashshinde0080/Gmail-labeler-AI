"""
Unit tests for free-tier rate-limit handling.

Everything here exercises pure helpers or the client's fail-fast path, so no
credentials and no network access are required.
"""

from __future__ import annotations

import time

import httpx
import pytest
from groq import RateLimitError

from app.ai import groq_client
from app.ai.groq_client import (
    GroqRateLimitedError,
    _cooldown_seconds,
    _parse_duration,
    _retry_after_seconds,
)
from app.config import get_settings


class TestParseDuration:
    def test_plain_seconds(self):
        assert _parse_duration("12") == 12.0

    def test_seconds_suffix(self):
        assert _parse_duration("1.2s") == pytest.approx(1.2)

    def test_milliseconds(self):
        assert _parse_duration("600ms") == pytest.approx(0.6)

    def test_minutes_and_seconds(self):
        assert _parse_duration("6m0s") == pytest.approx(360.0)

    def test_mixed_units(self):
        assert _parse_duration("2m30s") == pytest.approx(150.0)

    def test_garbage_returns_none(self):
        assert _parse_duration("soon") is None

    def test_empty_returns_none(self):
        assert _parse_duration("") is None


def _rate_limit_error(headers: dict[str, str]) -> RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, headers=headers, request=request)
    return RateLimitError("rate limited", response=response, body=None)


class TestRetryAfterParsing:
    def test_retry_after_header(self):
        assert _retry_after_seconds(_rate_limit_error({"retry-after": "15"})) == 15.0

    def test_token_window_uses_largest_hint(self):
        exc = _rate_limit_error(
            {"retry-after": "2", "x-ratelimit-reset-tokens": "6m0s"}
        )
        assert _retry_after_seconds(exc) == pytest.approx(360.0)

    def test_missing_headers_returns_none(self):
        assert _retry_after_seconds(_rate_limit_error({})) is None

    def test_non_http_error_returns_none(self):
        assert _retry_after_seconds(RuntimeError("boom")) is None


class TestCooldownClamp:
    def test_hint_under_cap_is_kept(self):
        assert _cooldown_seconds(5) == 5

    def test_hint_over_cap_is_clamped(self):
        cap = get_settings().groq_rate_limit_max_wait
        assert _cooldown_seconds(cap * 10) == cap

    def test_missing_hint_uses_cap(self):
        assert _cooldown_seconds(None) == get_settings().groq_rate_limit_max_wait


class TestCooldownCircuitBreaker:
    def test_call_fails_fast_while_cooling_down(self, monkeypatch):
        """A 429 cooldown must stop us re-hitting the API on every message."""
        monkeypatch.setattr(groq_client, "_cooldown_until", time.monotonic() + 30)

        with pytest.raises(GroqRateLimitedError) as excinfo:
            groq_client.call_groq("prompt", message_id="msg1")

        assert excinfo.value.retry_after is not None

    def test_remaining_is_zero_when_clear(self, monkeypatch):
        monkeypatch.setattr(groq_client, "_cooldown_until", 0.0)
        assert groq_client.rate_limit_cooldown_remaining() == 0.0
