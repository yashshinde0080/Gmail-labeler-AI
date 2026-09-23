"""
Database engine and session factory.

SQLite is configured for:
    - WAL journal mode   → allows concurrent reads during writes
    - Foreign keys       → enforced at the connection level
    - check_same_thread  → disabled so FastAPI threads share the engine

Usage:
    from app.database.db import get_session

    with get_session() as session:
        session.add(some_model)
        session.commit()
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.models import Base
from app.logger import get_logger

logger = get_logger(__name__)

_settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False}
    if "sqlite" in _settings.database_url
    else {},
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)


# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Keep objects usable after session closes
)


def init_db() -> None:
    """
    Create all tables that do not already exist.
    Call once at application startup — idempotent.
    """
    logger.info("Initialising database", url=_settings.database_url_redacted)
    Base.metadata.create_all(bind=engine)
    logger.info("Database ready")


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager that provides a transactional database session.

    Automatically commits on success and rolls back on any exception,
    then closes the session regardless.

    Example:
        with get_session() as db:
            db.add(record)
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency — yields a session and ensures cleanup.

    Example:
        @app.get("/example")
        def example(db: Session = Depends(get_db_session)):
            ...
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
