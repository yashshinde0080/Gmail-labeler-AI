"""
Gmail label management with fuzzy matching and local caching.

Key responsibilities:
    - Fetch all labels from Gmail and sync them to SQLite.
    - Find the best matching existing label for an AI-proposed name.
    - Create new labels when no match exceeds the fuzzy threshold.
    - Apply labels to messages.
    - Never create duplicate labels.
"""

from __future__ import annotations

import re

from googleapiclient.errors import HttpError
from thefuzz import fuzz, process

from app.config import get_settings
from app.database.crud import get_all_labels, upsert_label
from app.database.db import get_session
from app.gmail.service import get_gmail_service
from app.logger import get_logger

logger = get_logger(__name__)
_settings = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────
FUZZY_MATCH_THRESHOLD = 85  # Min score (0-100) for a name to be "the same" label
USER_ID = "me"


# ── Normalisation ─────────────────────────────────────────────────────────────


def _normalise(name: str) -> str:
    """
    Strip characters that make labels look different but mean the same thing:
    spaces, hyphens, underscores — then lower-case.

    'Work / AI', 'work-ai', 'WORK_AI' all become 'workai'.
    """
    return re.sub(r"[\s\-_/]+", "", name).lower()


# ── Fetch & Sync ──────────────────────────────────────────────────────────────


def fetch_and_sync_labels() -> dict[str, str]:
    """
    Pull all labels from Gmail, upsert them into SQLite, and return a
    mapping of {label_name: gmail_label_id}.

    Called at the start of every scheduler cycle to keep the cache fresh.
    """
    service = get_gmail_service()
    try:
        response = service.users().labels().list(userId=USER_ID).execute()
        gmail_labels = response.get("labels", [])
    except HttpError as exc:
        logger.error("Failed to fetch Gmail labels", error=str(exc))
        raise

    label_map: dict[str, str] = {}

    with get_session() as db:
        for gl in gmail_labels:
            lid = gl["id"]
            lname = gl["name"]
            upsert_label(db, gmail_label_id=lid, label_name=lname)
            label_map[lname] = lid

    logger.info("Label cache synced", count=len(label_map))
    return label_map


def get_cached_labels() -> dict[str, str]:
    """
    Return label name→id mapping from SQLite (the local cache).
    Falls back to fetch_and_sync_labels() if the cache is empty.
    """
    with get_session() as db:
        labels = get_all_labels(db)

    if not labels:
        return fetch_and_sync_labels()

    return {lbl.label_name: lbl.gmail_label_id for lbl in labels}


# ── Fuzzy Matching ────────────────────────────────────────────────────────────


def find_best_label_match(
    proposed_name: str,
    label_map: dict[str, str],
    threshold: int = FUZZY_MATCH_THRESHOLD,
) -> tuple[str | None, str | None, int]:
    """
    Fuzzy-match proposed_name against all known label names.

    Returns:
        (matched_label_name, gmail_label_id, score)
        If no match exceeds the threshold, all three values are None / 0.
    """
    if not label_map:
        return None, None, 0

    # Build a normalised version of the lookup dict
    norm_map: dict[str, str] = {_normalise(k): k for k in label_map}
    norm_proposed = _normalise(proposed_name)

    # Use token_sort_ratio to handle word-order differences
    result = process.extractOne(
        norm_proposed,
        list(norm_map.keys()),
        scorer=fuzz.token_sort_ratio,
    )

    if result is None:
        return None, None, 0

    matched_norm, score = result[0], result[1]

    if score < threshold:
        logger.debug(
            "No fuzzy match found",
            proposed=proposed_name,
            best=matched_norm,
            score=score,
            threshold=threshold,
        )
        return None, None, score

    original_name = norm_map[matched_norm]
    label_id = label_map[original_name]

    logger.debug(
        "Fuzzy match found",
        proposed=proposed_name,
        matched=original_name,
        score=score,
    )
    return original_name, label_id, score


# ── Create Label ──────────────────────────────────────────────────────────────


def create_label(name: str) -> tuple[str, str]:
    """
    Create a new Gmail label and persist it to SQLite.

    Returns:
        (label_name, gmail_label_id)

    Raises:
        HttpError if the Gmail API call fails.
    """
    service = get_gmail_service()

    label_body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }

    try:
        result = (
            service.users().labels().create(userId=USER_ID, body=label_body).execute()
        )
    except HttpError as exc:
        # 409 means the label already exists on Gmail's side — re-sync and retry
        if exc.resp.status == 409:
            logger.warning(
                "Label already exists on Gmail — re-syncing cache", name=name
            )
            label_map = fetch_and_sync_labels()
            matched, lid, _ = find_best_label_match(name, label_map, threshold=95)
            if matched and lid:
                return matched, lid
        logger.error("Failed to create Gmail label", name=name, error=str(exc))
        raise

    new_id = result["id"]
    new_name = result["name"]

    with get_session() as db:
        upsert_label(db, gmail_label_id=new_id, label_name=new_name, created_by_us=True)

    logger.info("Created new Gmail label", name=new_name, id=new_id)
    return new_name, new_id


# ── Apply / Remove Labels ─────────────────────────────────────────────────────


def apply_label_to_message(message_id: str, label_id: str) -> bool:
    """
    Add label_id to the given Gmail message.
    Returns True on success, False on non-fatal failure.
    """
    service = get_gmail_service()
    try:
        service.users().messages().modify(
            userId=USER_ID,
            id=message_id,
            body={"addLabelIds": [label_id], "removeLabelIds": []},
        ).execute()
        logger.debug("Label applied", message_id=message_id, label_id=label_id)
        return True
    except HttpError as exc:
        logger.error(
            "Failed to apply label",
            message_id=message_id,
            label_id=label_id,
            error=str(exc),
        )
        return False


def archive_message(message_id: str) -> bool:
    """
    Remove INBOX label — effectively archives the message.
    Returns True on success.
    """
    service = get_gmail_service()
    try:
        service.users().messages().modify(
            userId=USER_ID,
            id=message_id,
            body={"removeLabelIds": ["INBOX"], "addLabelIds": []},
        ).execute()
        logger.debug("Message archived", message_id=message_id)
        return True
    except HttpError as exc:
        logger.error("Failed to archive message", message_id=message_id, error=str(exc))
        return False


def star_message(message_id: str) -> bool:
    """
    Add the STARRED system label to a message.
    Returns True on success.
    """
    service = get_gmail_service()
    try:
        service.users().messages().modify(
            userId=USER_ID,
            id=message_id,
            body={"addLabelIds": ["STARRED"], "removeLabelIds": []},
        ).execute()
        logger.debug("Message starred", message_id=message_id)
        return True
    except HttpError as exc:
        logger.error("Failed to star message", message_id=message_id, error=str(exc))
        return False
