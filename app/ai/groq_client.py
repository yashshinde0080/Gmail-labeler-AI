"""
Groq API client with retry, pacing, and rate-limit handling.

Uses the official groq-python SDK which is OpenAI-compatible. Everything
related to retries, request pacing, and free-tier rate limits lives here so
the classifier and scheduler stay clean.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()


class GroqRateLimitedError(RuntimeError):
    """
    Raised when Groq's rate limit prevents a classification.

    This is a *transient* condition — the email was never classified — so
    callers should defer the message instead of treating the failure as bad
    data (which would permanently label the email "Uncategorised").

    Attributes:
        retry_after: Suggested wait in seconds, when Groq supplied one.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# ── Client singleton ──────────────────────────────────────────────────────────
_client: Groq | None = None


def get_groq_client() -> Groq:
    """Return a cached Groq client instance."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = Groq(
            api_key=_settings.groq_api_key,
            timeout=_settings.groq_timeout,
            max_retries=0,  # We handle retries ourselves via tenacity
        )
        logger.info("Groq client initialised", model=_settings.groq_model)
    return _client


# ── Rate-limit header parsing ─────────────────────────────────────────────────

_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def _parse_duration(value: str) -> float | None:
    """
    Parse a Groq duration header into seconds.

    Handles plain seconds ("12") and duration strings such as "1.2s",
    "600ms" or "6m0s". Returns None when the value is not understood.
    """
    value = (value or "").strip()
    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        pass

    total = 0.0
    found = False
    for amount, unit in _DURATION_PART_RE.findall(value):
        total += float(amount) * _UNIT_SECONDS[unit]
        found = True
    return total if found else None


def _retry_after_seconds(exc: BaseException) -> float | None:
    """
    Extract the suggested wait (in seconds) from a Groq error, if any.

    Groq sends ``retry-after`` alongside ``x-ratelimit-reset-requests`` and
    ``x-ratelimit-reset-tokens``; taking the largest keeps us on the safe
    side of whichever quota window was exhausted.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None

    hints: list[float] = []
    for header in (
        "retry-after",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
    ):
        parsed = _parse_duration(headers.get(header, ""))
        if parsed is not None:
            hints.append(parsed)
    return max(hints) if hints else None


def _cooldown_seconds(hinted: float | None) -> float:
    """Clamp a suggested wait to the configured maximum."""
    cap = float(_settings.groq_rate_limit_max_wait)
    return min(hinted, cap) if hinted is not None else cap


# ── Pacing & cooldown ─────────────────────────────────────────────────────────

_throttle_lock = threading.Lock()
_last_request_ts = float("-inf")
_cooldown_until = 0.0


def _throttle() -> None:
    """Space requests out so a burst cannot blow through the request limit."""
    interval = 60.0 / _settings.groq_requests_per_minute
    global _last_request_ts  # noqa: PLW0603
    with _throttle_lock:
        wait = interval - (time.monotonic() - _last_request_ts)
        if wait > 0:
            logger.debug("Pacing Groq request", wait_seconds=round(wait, 2))
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def rate_limit_cooldown_remaining() -> float:
    """Seconds left before Groq should be called again (0 when clear)."""
    return max(0.0, _cooldown_until - time.monotonic())


def _trip_cooldown(seconds: float) -> None:
    """Stop issuing calls for ``seconds`` so we do not hammer a 429."""
    global _cooldown_until  # noqa: PLW0603
    _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)


# ── API call with retry ───────────────────────────────────────────────────────

_EXPONENTIAL_WAIT = wait_exponential(multiplier=1, min=2, max=30)


def _wait_between_attempts(retry_state: RetryCallState) -> float:
    """Wait for Groq's suggested window, falling back to exponential back-off."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RateLimitError):
        hinted = _retry_after_seconds(exc)
        if hinted is not None:
            return _cooldown_seconds(hinted)
    return _EXPONENTIAL_WAIT(retry_state)


@retry(
    retry=retry_if_exception_type((APIConnectionError, RateLimitError)),
    stop=stop_after_attempt(max(1, _settings.groq_max_retries)),
    wait=_wait_between_attempts,
    reraise=True,
)
def _request_completion(prompt: str, message_id: str) -> dict[str, Any]:
    """
    Perform a single chat-completion request (with tenacity retries).

    Raises:
        APIConnectionError  — network problems (retried)
        RateLimitError      — free-tier limit hit (retried with back-off)
        APIStatusError      — 4xx/5xx from Groq (not retried)
    """
    _throttle()
    client = get_groq_client()
    start_ts = time.monotonic()

    logger.info(
        "Sending prompt to Groq",
        model=_settings.groq_model,
        message_id=message_id,
        prompt_chars=len(prompt),
    )

    try:
        response = client.chat.completions.create(
            model=_settings.groq_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise email classifier. "
                        "Always respond with valid JSON only. "
                        "Never include markdown code fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # Low temperature → deterministic, consistent
            max_tokens=512,  # Small models can be chatty — leave headroom
            top_p=0.9,
            # JSON mode. Supported by llama-3.1-8b-instant; requires the word
            # "JSON" to appear in the prompt (see prompt.py) and guarantees a
            # syntactically valid JSON object back.
            response_format={"type": "json_object"},
        )
    except RateLimitError:
        logger.warning("Groq rate limit hit — will retry", message_id=message_id)
        raise
    except APIConnectionError:
        logger.warning("Groq connection error — will retry", message_id=message_id)
        raise
    except APIStatusError as exc:
        logger.error(
            "Groq API error",
            status=exc.status_code,
            message_id=message_id,
            error=str(exc),
        )
        raise

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)
    usage = response.usage

    content = response.choices[0].message.content or ""

    logger.info(
        "Groq response received",
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )

    return {
        "content": content,
        "model": response.model,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
        "response_time_ms": elapsed_ms,
    }


def call_groq(prompt: str, message_id: str = "") -> dict[str, Any]:
    """
    Send a prompt to Groq and return the response dict documented on
    :func:`_request_completion`.

    Rate limits are turned into a :class:`GroqRateLimitedError` (carrying the
    suggested wait) so the pipeline can defer the message rather than label it
    with a bogus category. While a cooldown is active no API call is made at
    all — the error is raised immediately.
    """
    remaining = rate_limit_cooldown_remaining()
    if remaining > 0:
        logger.info(
            "Groq rate-limit cooldown active — skipping call",
            message_id=message_id,
            retry_in_seconds=round(remaining, 1),
        )
        raise GroqRateLimitedError(
            f"Groq rate-limit cooldown active for {remaining:.1f}s",
            retry_after=remaining,
        )

    try:
        return _request_completion(prompt, message_id)
    except RateLimitError as exc:
        hinted = _retry_after_seconds(exc)
        _trip_cooldown(_cooldown_seconds(hinted))
        logger.warning(
            "Groq rate limit exhausted — message will be deferred",
            message_id=message_id,
            retry_after_seconds=hinted,
        )
        raise GroqRateLimitedError("Groq rate limit reached", hinted) from exc
