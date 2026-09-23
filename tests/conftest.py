"""
Pytest bootstrap.

The application reads its configuration through ``pydantic-settings`` and
several modules cache it at import time, so the required environment
variables must exist *before* any ``app.*`` module is imported. This module
sets safe test values and creates the database schema so the suite runs
without a real ``.env`` file or any external credentials.

Environment variables already present in the process are left untouched,
which lets CI or a developer override any value.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ── Temporary, isolated data locations ────────────────────────────────────────
_tmp_root = Path(tempfile.gettempdir()) / "gmail_labeler_tests"
_tmp_root.mkdir(parents=True, exist_ok=True)

# Fresh database file for every test session.
_db_path = _tmp_root / "test.db"
_db_path.unlink(missing_ok=True)

# ── Required settings (must precede any app import) ───────────────────────────
os.environ.setdefault("GROQ_API_KEY", "test_key_not_real")
os.environ.setdefault("GMAIL_CLIENT_ID", "test_client_id")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path.as_posix()}")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DATA_DIR", str(_tmp_root))
os.environ.setdefault("LOG_DIR", str(_tmp_root / "logs"))

# FERNET_KEY must be a valid 32-byte url-safe base64 key.
if not os.environ.get("FERNET_KEY"):
    from cryptography.fernet import Fernet

    os.environ["FERNET_KEY"] = Fernet.generate_key().decode()

# ── Create the schema before tests run ────────────────────────────────────────
from app.database.db import init_db  # noqa: E402

init_db()
