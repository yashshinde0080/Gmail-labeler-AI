"""
Central configuration — all values read from environment / .env file.
Every other module imports from here; nothing else touches os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables or a .env file.
    Pydantic-settings automatically coerces types and validates values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Groq AI ──────────────────────────────────────────────────────────
    groq_api_key: str = Field(..., description="Groq API key")
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq model identifier",
    )
    groq_timeout: int = Field(default=30, description="Request timeout in seconds")
    groq_max_retries: int = Field(default=3, description="Max retries for Groq calls")

    # ── Gmail OAuth2 ─────────────────────────────────────────────────────
    gmail_client_id: str = Field(..., description="Google OAuth2 client ID")
    gmail_client_secret: str = Field(..., description="Google OAuth2 client secret")
    gmail_project_id: str = Field(default="yash-gmail-501912")
    gmail_auth_uri: str = Field(default="https://accounts.google.com/o/oauth2/auth")
    gmail_token_uri: str = Field(default="https://oauth2.googleapis.com/token")
    gmail_cert_url: str = Field(default="https://www.googleapis.com/oauth2/v1/certs")
    gmail_redirect_uri: str = Field(default="http://localhost:8000")
    gcp_pubsub_topic: str = Field(default="projects/YOUR_PROJECT_ID/topics/YOUR_TOPIC_ID")
    gmail_scopes: str = Field(default="https://www.googleapis.com/auth/gmail.modify")

    # ── Database & Security ──────────────────────────────────────────────
    database_url: str = Field(default="postgresql://user:password@localhost/gmail_db")
    fernet_key: str = Field(..., description="Fernet key for encrypting tokens")

    # ── Scheduler ────────────────────────────────────────────────────────
    poll_interval_seconds: int = Field(default=10, ge=10, le=3600)
    scheduler_enabled: bool = Field(default=True)
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_backoff_base: int = Field(default=2, ge=1)

    # ── Classification ───────────────────────────────────────────────────
    confidence_threshold: int = Field(default=70, ge=0, le=100)
    archive_low_importance: bool = Field(default=True)
    star_high_importance: bool = Field(default=True)
    enable_nested_labels: bool = Field(default=False)

    # ── API ──────────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    cors_origins: str = Field(default="*")
    log_level: str = Field(default="INFO")

    # ── Paths ────────────────────────────────────────────────────────────
    data_dir: str = Field(default="./data")
    log_dir: str = Field(default="./data/logs")

    # ── Derived properties ───────────────────────────────────────────────
    @property
    def scopes_list(self) -> list[str]:
        """Split comma-separated scopes into a proper list."""
        return [s.strip() for s in self.gmail_scopes.split(",")]

    @property
    def cors_origins_list(self) -> list[str]:
        """Split comma-separated CORS origins into a list."""
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",")]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    def ensure_directories(self) -> None:
        """Create all required data directories on startup."""
        for path_str in (self.data_dir, self.log_dir):
            path = Path(path_str)
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.
    Using lru_cache means the .env file is read exactly once per process.
    """
    settings = Settings()
    settings.ensure_directories()
    return settings
