"""Daily-cached check against GitHub Releases for a newer ``setplot``.

Cached so we don't hit GitHub on every CLI invocation. Best-effort and silent
on failure: if the network is down, the API rate-limits us, or the response
isn't parseable, we just don't print an upgrade hint.

Disable entirely by setting ``SETPLOT_NO_UPDATE_CHECK=1``. Override the
upstream repo by setting ``SETPLOT_GITHUB_REPO=owner/name`` (rarely needed).
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Any

# Author-configured upstream. Bake-time constant — release-pipeline aware
# overrides (env var below) keep this honest for forks.
UPSTREAM_GITHUB_REPO = "robobrien/setplot"
CHECK_INTERVAL_HOURS = 24
HTTP_TIMEOUT_S = 2.5


def _cache_path() -> Path:
    """Stamp file lives under the data dir so it survives uv tool re-installs
    but doesn't pollute $HOME directly."""
    from setplot.config import get_settings

    return get_settings().data_dir() / ".update-check.json"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _load_cache() -> dict[str, Any] | None:
    p = _cache_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(latest: str) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"checked_at": _utcnow().isoformat(), "latest_version": latest}))
    except OSError:
        pass  # cache is a best-effort optimisation


def _fetch_latest_version(repo: str) -> str | None:
    """Hit the public GitHub Releases API. Returns the version string (no v) or None."""
    import urllib.error
    import urllib.request

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "setplot-update-check"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    tag = data.get("tag_name") or ""
    return tag.lstrip("v") if tag else None


def _is_newer(latest: str, current: str) -> bool:
    """Naive PEP-440-ish dotted-int compare. Returns True if ``latest`` strictly newer.

    Returns False when either side fails to parse — being silent is safer than
    nagging users to "upgrade" because their version string was malformed.
    """

    def parts(v: str) -> tuple[int, ...]:
        # Strip pre/post/dev suffixes — best-effort. e.g. "0.2.0a1" → (0, 2, 0)
        m = re.match(r"^(\d+(?:\.\d+)*)", v.strip())
        if not m:
            return ()
        return tuple(int(x) for x in m.group(1).split("."))

    pl, pc = parts(latest), parts(current)
    if not pl or not pc:
        return False
    return pl > pc


def check(current_version: str) -> str | None:
    """Return a one-line upgrade hint if a newer release is available, else None.

    Reads from cache to keep CLI invocations snappy; refreshes once per
    ``CHECK_INTERVAL_HOURS``. Honours ``SETPLOT_NO_UPDATE_CHECK``.
    """
    if os.environ.get("SETPLOT_NO_UPDATE_CHECK") in ("1", "true", "yes"):
        return None
    repo = os.environ.get("SETPLOT_GITHUB_REPO") or UPSTREAM_GITHUB_REPO

    cache = _load_cache()
    latest: str | None = None
    if cache:
        try:
            checked_at = datetime.datetime.fromisoformat(cache["checked_at"])
            age_h = (_utcnow() - checked_at).total_seconds() / 3600
            if age_h < CHECK_INTERVAL_HOURS:
                latest = cache.get("latest_version")
        except (ValueError, KeyError, TypeError):
            pass

    if latest is None:
        latest = _fetch_latest_version(repo)
        if latest:
            _save_cache(latest)

    if latest and _is_newer(latest, current_version):
        return f"  → newer release available: {latest}. Upgrade with `uv tool upgrade setplot`."
    return None
