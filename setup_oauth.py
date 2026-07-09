"""
One-time OAuth setup.

Generates data/token.json required for Gmail API access.

Usage:
    python setup_oauth.py

Opens a browser for Google login, then saves token to data/token.json.
"""

from __future__ import annotations

import json
import pathlib
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

creds_path = pathlib.Path("data/credentials.json")
if not creds_path.exists():
    print(f"ERROR: {creds_path} not found.")
    print("Download your OAuth client JSON from Google Cloud Console")
    print("and save it to data/credentials.json")
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
creds = flow.run_local_server(port=8000, open_browser=True)

token_data = {
    "token": creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri": creds.token_uri,
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "scopes": list(creds.scopes) if creds.scopes else [],
    "expiry": creds.expiry.isoformat() if creds.expiry else None,
}

token_path = pathlib.Path("data/token.json")
token_path.parent.mkdir(parents=True, exist_ok=True)
token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")

print(f"\nSaved to {token_path}")
print("Gmail access ready. Start the app with:")
print("  uvicorn app.main:app --reload --port 8000")
