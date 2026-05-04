"""On-disk token storage for streaming integrations.

Single-user model: tokens live under ``$HOME/.setplot/`` (override via the
``SETPLOT_TOKEN_DIR`` env var, useful for tests). Files are written 0600 so a
shared host account can't sidestep the unix permissions.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def token_dir() -> Path:
    override = os.environ.get("SETPLOT_TOKEN_DIR")
    if override:
        return Path(override)
    return Path.home() / ".setplot"


def _path(service: str) -> Path:
    return token_dir() / f"{service}_token.json"


def load(service: str) -> dict[str, Any] | None:
    p = _path(service)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save(service: str, payload: dict[str, Any]) -> None:
    """Persist a token bundle. Adds ``stored_at`` so callers can decide when
    to refresh; writes the file 0600 to keep the access token off other users."""
    d = token_dir()
    d.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "chmod"):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    p = _path(service)
    body = {**payload, "stored_at": int(time.time())}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body))
    if hasattr(os, "chmod"):
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    tmp.replace(p)


def clear(service: str) -> None:
    p = _path(service)
    if p.exists():
        p.unlink()


def is_expired(payload: dict[str, Any], skew_s: int = 60) -> bool:
    """True if the access token is past its expiry (with a small safety skew)."""
    if not payload:
        return True
    stored_at = payload.get("stored_at")
    expires_in = payload.get("expires_in")
    if not stored_at or not expires_in:
        return True
    return time.time() >= (stored_at + expires_in - skew_s)
