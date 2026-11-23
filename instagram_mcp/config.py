"""Settings and access-token storage.

The token is read from `.env` (IG_ACCESS_TOKEN) the first time. After each
refresh the new token is written to `data/token.json`, which then wins over
`.env`. Both files are git-ignored. The token is never printed or returned
by any tool.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("IG_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN_FILE = DATA_DIR / "token.json"
DB_FILE = DATA_DIR / "instagram.db"

API_VERSION = os.getenv("IG_API_VERSION", "v26.0")
GRAPH = f"https://graph.instagram.com/{API_VERSION}"
GRAPH_UNVERSIONED = "https://graph.instagram.com"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{API_VERSION}"

# Refresh the long-lived token when the last refresh is older than this.
# A refreshed token is valid for 60 days, so weekly is very safe.
REFRESH_EVERY_SECONDS = 7 * 24 * 3600


@dataclass
class TokenState:
    token: str
    refreshed_at: float  # unix time of the last successful refresh (0 = never)


def load_token() -> TokenState:
    if TOKEN_FILE.exists():
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if data.get("token"):
            return TokenState(data["token"], float(data.get("refreshed_at", 0)))
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "No Instagram access token found. Put IG_ACCESS_TOKEN=... in the .env "
            "file next to README.md (see .env.example)."
        )
    return TokenState(token, 0.0)


def save_token(token: str) -> None:
    TOKEN_FILE.write_text(
        json.dumps({"token": token, "refreshed_at": time.time()}), encoding="utf-8"
    )
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass  # Windows: file permissions work differently; the file is still git-ignored
