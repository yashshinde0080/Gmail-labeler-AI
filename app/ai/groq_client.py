"""
Groq API client with retry, timeout, and token tracking.

Uses the official groq-python SDK which is OpenAI-compatible.
All retries are managed here so the classifier stays clean.
"""

from __future__ import annotations

import time
from typing import Any

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

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


# ── API call with retry ───────────────────────────────────────────────────────


@retry(
    retry=retry_if_exception_type((APIConnectionError, RateLimitError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def call_groq(prompt: str, message_id: str = "") -> dict[str, Any]:
    """
    Send a prompt to Groq and return a dict containing:
        {
            "content": "<raw model output string>",
            "model": "<model name>",
            "prompt_tokens": int,
            "completion_tokens": int,
            "total_tokens": int,
            "response_time_ms": int,
        }

    Raises:
        APIConnectionError  — network problems (retried)
        RateLimitError      — free-tier limit hit (retried with back-off)
        APIStatusError      — 4xx/5xx from Groq (not retried)
    """
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
            max_tokens=256,  # Classification JSON is small
            top_p=0.9,
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
