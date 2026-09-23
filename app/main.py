"""
Application entry point.

Responsibilities:
    1. Configure structured logging.
    2. Initialise the SQLite database.
    3. Start the APScheduler background scheduler.
    4. Mount the FastAPI application with CORS and API routes.
    5. Register a lifespan handler for clean startup/shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api import router
from app.config import get_settings
from app.database.db import init_db
from app.logger import get_logger, setup_logging
from app.scheduler import start_scheduler, stop_scheduler

limiter = Limiter(key_func=get_remote_address)

# ── Bootstrap logging before anything else ────────────────────────────────────
setup_logging()
logger = get_logger(__name__)
_settings = get_settings()

# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Async context manager that wraps the application lifetime.

    Everything before `yield` runs on startup.
    Everything after `yield` runs on shutdown.
    """
    # ── Startup ───────────────────────────────────────────────────────────
    logger.info(
        "Gmail AI Auto Labeler starting",
        model=_settings.groq_model,
        poll_interval_seconds=_settings.poll_interval_seconds,
        database=_settings.database_url_redacted,
    )

    init_db()
    logger.info("Database initialised")

    start_scheduler()

    logger.info(
        "Application ready",
        host=_settings.api_host,
        port=_settings.api_port,
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Shutting down Gmail AI Auto Labeler")
    stop_scheduler()
    logger.info("Shutdown complete")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Gmail AI Auto Labeler",
    description=(
        "Continuously monitors Gmail, classifies emails with Groq AI, "
        "creates labels, stars important messages, and archives clutter."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(router)


# ── Dev entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=_settings.api_host,
        port=_settings.api_port,
        reload=False,
        log_level=_settings.log_level.lower(),
        access_log=True,
    )
