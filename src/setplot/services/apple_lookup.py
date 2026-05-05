"""iTunes Search API lookup — fetches preview URLs for Apple Music song IDs.

Public, unauthenticated endpoint. Single-user use stays well under iTunes'
informal rate limits (we cache responses for 24h on disk per song id).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
CACHE_TTL_S = 24 * 60 * 60


def _cache_path(cache_dir: Path, song_id: str) -> Path:
    return cache_dir / "preview_cache" / f"{song_id}.json"


def lookup(song_id: str, *, cache_dir: Path) -> dict[str, Any] | None:
    """Return ``{preview_url, artwork_url, track_name, artist_name}`` for a
    song id, or ``None`` if not found. Cached to disk for 24h."""
    if not song_id:
        return None

    cp = _cache_path(cache_dir, song_id)
    if cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL_S:
        try:
            cached = json.loads(cp.read_text())
            # Sentinel "not found" cached as empty dict — return None on hit.
            return cached or None
        except (json.JSONDecodeError, OSError):
            pass

    qs = urllib.parse.urlencode({"id": song_id})
    req = urllib.request.Request(
        f"{ITUNES_LOOKUP_URL}?{qs}",
        headers={"User-Agent": "SetPlot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except (OSError, json.JSONDecodeError):
        return None

    results = payload.get("results") or []
    track = next((r for r in results if r.get("kind") == "song"), None)

    cp.parent.mkdir(parents=True, exist_ok=True)
    if not track:
        cp.write_text("{}")
        return None

    out = {
        "preview_url": track.get("previewUrl") or "",
        "artwork_url": track.get("artworkUrl100") or "",
        "track_name": track.get("trackName") or "",
        "artist_name": track.get("artistName") or "",
    }
    cp.write_text(json.dumps(out))
    return out
