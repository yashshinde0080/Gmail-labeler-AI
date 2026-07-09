"""
Gmail API service builder.

Provides a get_gmail_service() factory that returns an authenticated
googleapiclient Resource.  All other Gmail modules import from here.
"""

from __future__ import annotations

from googleapiclient.discovery import Resource, build

from app.gmail.auth import get_credentials
from app.logger import get_logger

logger = get_logger(__name__)


def get_gmail_service() -> Resource:
    """
    Build and return a Gmail API service resource.

    The Resource is lightweight to construct and the credentials are
    cached, so calling this multiple times per scheduler cycle is fine.
    """
    creds = get_credentials()
    service = build(
        "gmail",
        "v1",
        credentials=creds,
        cache_discovery=False,  # Avoid file-system discovery cache issues in Docker
    )
    logger.debug("Gmail service built successfully")
    return service
