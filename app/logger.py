"""
Structured logging setup using structlog.

Every module should do:
    from app.logger import get_logger
    logger = get_logger(__name__)

Log entries are emitted as JSON in production and as coloured
key=value pairs during local development, controlled by LOG_LEVEL.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog

from app.config import get_settings


def _configure_stdlib_logging(log_level: str, log_dir: str) -> None:
    """
    Wire up the standard-library logging so that third-party libraries
    (google-auth, httpx, apscheduler …) also flow through structlog.
    """
    import os

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]
    
    if os.getenv("VERCEL") != "1":
        log_path = Path(log_dir) / "app.log"
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level, logging.INFO),
        handlers=handlers,
        force=True,
    )

    # Quieten noisy libraries
    for noisy in ("googleapiclient", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def setup_logging() -> None:
    """
    Call once at application startup.
    Configures both structlog and the stdlib root logger.
    """
    settings = get_settings()
    _configure_stdlib_logging(settings.log_level, settings.log_dir)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # Enforce machine-parseable JSON for production / Docker / CI
    renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *shared_processors,
            renderer,
        ]
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Keep the file handler from _configure_stdlib_logging alongside structlog
    root_logger.handlers = [
        h for h in root_logger.handlers if isinstance(h, logging.FileHandler)
    ]
    root_logger.handlers.append(handler)
    root_logger.setLevel(settings.log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name."""
    return structlog.get_logger(name)
