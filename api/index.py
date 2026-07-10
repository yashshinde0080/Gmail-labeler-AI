"""
Vercel serverless entrypoint.

Re-exports the FastAPI app so @vercel/python can serve it as ASGI.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path so imports work on Vercel
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402, F401
