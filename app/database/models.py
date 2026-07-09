"""
SQLAlchemy ORM models.

Five tables:
    processed_emails  — one row per email handled by the system
    labels            — Gmail labels we have created or know about
    ai_logs           — token usage and latency for every Groq call
    retries           — tracks failed messages and retry state
    app_settings      — simple key/value store for runtime config
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


class ProcessedEmail(Base):
    """
    Records every email that has passed through the classifier.
    The gmail_message_id is unique so we can cheaply detect duplicates.
    """

    __tablename__ = "processed_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gmail_message_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recipient: Mapped[str | None] = mapped_column(String(512), nullable=True)
    received_date: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # AI output
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    importance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    label_applied: Mapped[str | None] = mapped_column(String(256), nullable=True)
    gmail_label_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Processing metadata
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="success", nullable=False
    )  # success | failed | skipped

    def __repr__(self) -> str:
        return (
            f"<ProcessedEmail id={self.id} "
            f"msg={self.gmail_message_id!r} "
            f"category={self.category!r}>"
        )


class Label(Base):
    """
    Mirrors Gmail labels we have created or discovered.
    Cached here so we avoid repeated Gmail API list calls.
    """

    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("label_name", name="uq_label_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gmail_label_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label_name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_by_us: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Label id={self.id} name={self.label_name!r}>"


class AILog(Base):
    """
    Tracks token consumption and latency for every Groq API call.
    Useful for monitoring free-tier usage and debugging slow responses.
    """

    __tablename__ = "ai_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AILog id={self.id} model={self.model!r} tokens={self.total_tokens}>"


class Retry(Base):
    """
    Tracks emails that failed processing so the scheduler can retry them.
    Once retry_count exceeds the configured maximum the row status becomes
    'abandoned' and the message is skipped in future runs.
    """

    __tablename__ = "retries"
    __table_args__ = (UniqueConstraint("message_id", name="uq_retry_message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="pending"
    )  # pending | abandoned
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<Retry id={self.id} msg={self.message_id!r} "
            f"count={self.retry_count} status={self.status!r}>"
        )


class AppSetting(Base):
    """
    Key/value store for runtime-mutable application settings.
    Seeded on first run; overrides .env values at runtime when present.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AppSetting key={self.key!r} value={self.value!r}>"
