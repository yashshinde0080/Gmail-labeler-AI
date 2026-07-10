"""
Gmail message fetching, parsing, and cleaning.

Responsibilities:
    - List new inbox messages (excluding spam / trash / already processed).
    - Download full message payloads.
    - Extract subject, sender, recipient, date, plain text, HTML, attachments.
    - Clean / sanitise the text before it is sent to the AI.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from bs4 import BeautifulSoup
from googleapiclient.errors import HttpError

from app.config import get_settings
from app.database.crud import is_already_processed
from app.database.db import get_session
from app.gmail.service import get_gmail_service
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

USER_ID = "me"
MAX_BODY_CHARS = 4000  # Truncate very long email bodies before sending to AI


# ── Gmail Push Notifications (Watch & History) ──────────────────────────────────

def start_watch() -> dict[str, Any]:
    """
    Start the Gmail push notification watch.
    Requires GCP_PUBSUB_TOPIC to be set in config.
    """
    service = get_gmail_service()
    topic = _settings.gcp_pubsub_topic
    
    if not topic or topic == "projects/YOUR_PROJECT_ID/topics/YOUR_TOPIC_ID":
        logger.warning("GCP_PUBSUB_TOPIC not configured — push notifications will not work.")
        return {}
    
    request_body = {
        "labelIds": ["INBOX"],
        "labelFilterAction": "include",
        "topicName": topic
    }
    
    try:
        response = service.users().watch(userId=USER_ID, body=request_body).execute()
        logger.info("Gmail watch started successfully", history_id=response.get("historyId"))
        return response
    except HttpError as exc:
        logger.error("Failed to start Gmail watch", error=str(exc))
        raise


def get_new_messages_from_history(start_history_id: str) -> tuple[list[str], str | None]:
    """
    Fetch message IDs added since start_history_id.
    Returns (list_of_message_ids, new_history_id).
    If start_history_id is invalid (e.g., expired), returns ([], None).
    """
    service = get_gmail_service()
    try:
        response = service.users().history().list(
            userId=USER_ID, 
            startHistoryId=start_history_id,
            historyTypes=["messageAdded"]
        ).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            logger.warning("History ID expired", start_history_id=start_history_id)
            return [], None
        logger.error("History API failed", error=str(exc))
        raise
        
    history = response.get("history", [])
    new_history_id = response.get("historyId")
    message_ids = []
    
    for record in history:
        for message_added in record.get("messagesAdded", []):
            msg = message_added.get("message", {})
            if msg.get("id"):
                message_ids.append(msg["id"])
                
    # Filter out duplicates (Gmail API can sometimes return the same ID multiple times in history)
    message_ids = list(set(message_ids))
    
    # Filter out IDs we have already processed
    with get_session() as db:
        new_ids = [mid for mid in message_ids if not is_already_processed(db, mid)]
        
    logger.info(
        "History sync complete",
        start_history_id=start_history_id,
        new_history_id=new_history_id,
        found=len(message_ids),
        new=len(new_ids),
    )
    return new_ids, new_history_id





# ── Download & Parse ──────────────────────────────────────────────────────────


def get_message_detail(message_id: str) -> dict[str, Any] | None:
    """
    Download the full message payload from Gmail and return a structured dict.

    Returns None if the download fails so the caller can record a retry.
    """
    service = get_gmail_service()

    try:
        raw = (
            service.users()
            .messages()
            .get(userId=USER_ID, id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        logger.error("Failed to fetch message", message_id=message_id, error=str(exc))
        return None

    headers = _extract_headers(raw.get("payload", {}).get("headers", []))
    plain_text, html_text, attachments = _extract_body(raw.get("payload", {}))
    cleaned = _clean_text(plain_text or _html_to_text(html_text or ""))

    return {
        "message_id": message_id,
        "thread_id": raw.get("threadId"),
        "subject": headers.get("subject", "(no subject)"),
        "sender": headers.get("from", ""),
        "recipient": headers.get("to", ""),
        "date": headers.get("date", ""),
        "plain_text": plain_text,
        "html_text": html_text,
        "cleaned_text": cleaned,
        "attachments": attachments,
        "snippet": raw.get("snippet", ""),
    }


# ── Header Extraction ─────────────────────────────────────────────────────────


def _extract_headers(headers: list[dict[str, str]]) -> dict[str, str]:
    """Return a normalised dict of important headers."""
    wanted = {"subject", "from", "to", "date", "message-id"}
    result: dict[str, str] = {}
    for h in headers:
        key = h.get("name", "").lower()
        if key in wanted:
            result[key] = h.get("value", "")
    return result


# ── Body Extraction ───────────────────────────────────────────────────────────


def _extract_body(
    payload: dict[str, Any],
) -> tuple[str | None, str | None, list[str]]:
    """
    Recursively walk a Gmail MIME payload tree.

    Returns:
        (plain_text, html_text, list_of_attachment_filenames)
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[str] = []

    def _walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        filename = part.get("filename", "")

        if filename:
            attachments.append(filename)
            return  # Don't try to decode attachment bodies

        body_data = part.get("body", {}).get("data")

        if mime == "text/plain" and body_data:
            plain_parts.append(_decode_b64(body_data))

        elif mime == "text/html" and body_data:
            html_parts.append(_decode_b64(body_data))

        for sub in part.get("parts", []):
            _walk(sub)

    _walk(payload)

    plain = "\n".join(plain_parts) if plain_parts else None
    html = "\n".join(html_parts) if html_parts else None
    return plain, html, attachments


def _decode_b64(data: str) -> str:
    """URL-safe base64 decode used by Gmail API."""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


# ── HTML → Plain Text ─────────────────────────────────────────────────────────


def _html_to_text(html: str) -> str:
    """Extract readable text from an HTML email body."""
    try:
        soup = BeautifulSoup(html, "lxml")
        # Remove script and style blocks entirely
        for tag in soup(["script", "style", "head", "meta", "link"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        return html  # Fallback: return raw if parsing fails


# ── Text Cleaning ─────────────────────────────────────────────────────────────


def _clean_text(text: str) -> str:
    """
    Sanitise and truncate email body text for the AI prompt.

    Operations performed:
        1. Collapse multiple blank lines into one.
        2. Strip excessive whitespace.
        3. Remove common unsubscribe / tracking boilerplate lines.
        4. Truncate to MAX_BODY_CHARS.
    """
    if not text:
        return ""

    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove HTML entities that survived parsing
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)

    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    # Remove tracking pixel URLs and typical footer boilerplate
    boilerplate_patterns = [
        r"unsubscribe.*?click here.*",
        r"view.*?in.*?browser.*",
        r"you.*?receive.*?this.*?because.*",
        r"©\s*\d{4}.*",
    ]
    for pattern in boilerplate_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    return text[:MAX_BODY_CHARS].strip()
