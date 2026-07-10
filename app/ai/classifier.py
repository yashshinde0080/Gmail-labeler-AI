"""
Email classification orchestrator.

This module ties together:
    - Prompt building (prompt.py)
    - Groq API call (groq_client.py)
    - JSON validation and sanitisation
    - SQLite logging (crud.py)

The classify_email() function is the single entry point called by the
scheduler for each new message.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.ai.groq_client import call_groq
from app.ai.prompt import build_classification_prompt
from app.config import get_settings
from app.database.crud import create_ai_log
from app.database.db import get_session
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# ── Type alias ────────────────────────────────────────────────────────────────
ClassificationResult = dict[str, Any]

# ── Default fallback result ───────────────────────────────────────────────────
_FALLBACK: ClassificationResult = {
    "category": "Uncategorised",
    "confidence": 0,
    "importance": "low",
    "archive": False,
    "star": False,
    "create_label": True,
    "new_label": "Uncategorised",
    "reason": "AI classification failed — manual review needed",
}


def classify_email(email_data: dict[str, Any]) -> ClassificationResult:
    """
    Classify a single email using the Groq LLM.

    Steps:
        1. Build the classification prompt.
        2. Call Groq.
        3. Parse and validate the JSON response.
        4. Apply business-rule overrides (confidence threshold etc.).
        5. Log the result to SQLite.
        6. Return the result dict.

    Always returns a valid ClassificationResult — never raises.
    """
    message_id = email_data.get("message_id", "unknown")
    start = time.monotonic()

    prompt = build_classification_prompt(email_data)

    # ── Call Groq ─────────────────────────────────────────────────────────
    groq_result: dict[str, Any] | None = None
    ai_success = False
    error_msg: str | None = None

    try:
        groq_result = call_groq(prompt, message_id=message_id)
        ai_success = True
    except Exception as exc:
        error_msg = str(exc)
        logger.error(
            "Groq call failed",
            message_id=message_id,
            error=error_msg,
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # ── Parse JSON ────────────────────────────────────────────────────────
    classification = _FALLBACK.copy()

    if groq_result:
        parsed = _parse_json(groq_result["content"], message_id)
        if parsed:
            classification = _validate_and_sanitise(parsed, message_id)

    # ── Apply confidence threshold ────────────────────────────────────────
    if classification["confidence"] < _settings.confidence_threshold:
        logger.warning(
            "Confidence below threshold — using fallback category",
            message_id=message_id,
            confidence=classification["confidence"],
            threshold=_settings.confidence_threshold,
        )
        # Keep the AI decision but reduce its authority
        classification["importance"] = "low"
        classification["archive"] = False
        classification["star"] = False

    # ── Apply global settings overrides ──────────────────────────────────
    if not _settings.archive_low_importance:
        classification["archive"] = False

    if not _settings.star_high_importance:
        classification["star"] = False

    # ── Log to SQLite ─────────────────────────────────────────────────────
    _log_ai_call(
        message_id=message_id,
        groq_result=groq_result,
        classification=classification,
        elapsed_ms=elapsed_ms,
        success=ai_success,
        error=error_msg,
    )

    logger.info(
        "Email classified",
        message_id=message_id,
        category=classification["category"],
        confidence=classification["confidence"],
        importance=classification["importance"],
        archive=classification["archive"],
        star=classification["star"],
    )

    return classification


def _parse_json(content: str, message_id: str) -> dict[str, Any] | None:
    """
    Extract and parse a JSON object from the model's raw output.

    The model sometimes wraps JSON in markdown code fences despite
    instructions — we handle that gracefully.
    """
    if not content:
        return None

    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", content).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()

    # Find the first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        logger.warning(
            "No JSON object found in model response",
            message_id=message_id,
            content_preview=content[:200],
        )
        return None

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as exc:
        logger.warning(
            "JSON parse error",
            message_id=message_id,
            error=str(exc),
            content_preview=content[:200],
        )
        return None


def _validate_and_sanitise(
    data: dict[str, Any], message_id: str
) -> ClassificationResult:
    """
    Validate each field returned by the model and apply safe defaults.

    This prevents bad AI output from corrupting Gmail state.
    """
    result: ClassificationResult = {}

    # category — must be a non-empty string; strip dangerous chars
    category = str(data.get("category", "Uncategorised")).strip()
    category = category[:64]
    result["category"] = category or "Uncategorised"

    # confidence — integer 0-100
    try:
        confidence = int(data.get("confidence", 0))
        result["confidence"] = max(0, min(100, confidence))
    except (TypeError, ValueError):
        result["confidence"] = 0

    # importance — must be one of three values
    importance = str(data.get("importance", "low")).lower()
    result["importance"] = (
        importance if importance in {"high", "medium", "low"} else "low"
    )

    # booleans
    result["archive"] = bool(data.get("archive", False))
    result["star"] = bool(data.get("star", False))
    result["create_label"] = bool(data.get("create_label", False))

    # new_label — only if create_label is True
    new_label = str(data.get("new_label", "")).strip()
    new_label = new_label[:64]
    result["new_label"] = new_label if result["create_label"] else ""

    # reason — plain text, truncate
    result["reason"] = str(data.get("reason", ""))[:512]

    return result


def _log_ai_call(
    message_id: str,
    groq_result: dict[str, Any] | None,
    classification: ClassificationResult,
    elapsed_ms: int,
    success: bool,
    error: str | None,
) -> None:
    """Write an AI call record to the ai_logs table."""
    try:
        with get_session() as db:
            create_ai_log(
                db,
                {
                    "gmail_message_id": message_id,
                    "model": groq_result["model"]
                    if groq_result
                    else _settings.groq_model,
                    "prompt_tokens": groq_result["prompt_tokens"] if groq_result else 0,
                    "completion_tokens": groq_result["completion_tokens"]
                    if groq_result
                    else 0,
                    "total_tokens": groq_result["total_tokens"] if groq_result else 0,
                    "response_time_ms": elapsed_ms,
                    "confidence": classification.get("confidence", 0),
                    "success": success,
                    "error_message": error,
                },
            )
    except Exception as exc:
        # Never let logging errors abort the main flow
        logger.error("Failed to write AI log", error=str(exc))
