"""Spotify Web API client used by the export endpoint.

Track resolution priority:
1. Existing ``spotify`` ID from ACR — use directly.
2. ISRC search — ``q=isrc:<isrc>``.
3. Title + artist search with a substring sanity check on the artist field
   (so a recognized "DJ Snake — Lean On" doesn't match "Lean On Me" when
   ACR confidence is borderline).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.spotify.com/v1"


def _request(token: str, method: str, path: str, body: Any = None) -> dict[str, Any]:
    url = f"{API}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _safe_request(token: str, method: str, path: str, body: Any = None) -> dict[str, Any] | None:
    """Like ``_request`` but turns 4xx into ``None`` (search miss is normal)
    and lets 5xx propagate as an error."""
    try:
        return _request(token, method, path, body)
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        if 400 <= e.code < 500:
            return None
        raise


def me(token: str) -> dict[str, Any]:
    return _request(token, "GET", "/me")


def resolve_track(token: str, tk: dict[str, Any]) -> tuple[str | None, str]:
    """Map one merged-track entry to a Spotify track URI.

    Returns ``(uri_or_None, reason)`` where reason is one of
    ``"acr_id"``, ``"isrc"``, ``"search"``, ``"unmatched"``.
    """
    sp_id = tk.get("spotify") or ""
    if sp_id:
        return f"spotify:track:{sp_id}", "acr_id"

    isrc = tk.get("isrc") or ""
    if isrc:
        q = urllib.parse.quote(f"isrc:{isrc}")
        result = _safe_request(token, "GET", f"/search?type=track&limit=1&q={q}")
        items = ((result or {}).get("tracks") or {}).get("items") or []
        if items:
            return items[0]["uri"], "isrc"

    title = (tk.get("title") or "").strip()
    artist = (tk.get("artists") or "").strip()
    if title and artist:
        q = urllib.parse.quote(f'track:"{title}" artist:"{artist}"')
        result = _safe_request(token, "GET", f"/search?type=track&limit=5&q={q}")
        items = ((result or {}).get("tracks") or {}).get("items") or []
        # Require the recognized artist to appear in one of Spotify's artist
        # names — guards against bad collisions when titles are common.
        artist_lc = artist.lower()
        for it in items:
            sp_artists = " ".join(a.get("name", "") for a in it.get("artists") or []).lower()
            if any(piece and piece in sp_artists for piece in artist_lc.split(",")):
                return it["uri"], "search"
        if items:  # weakest match — title-only
            return items[0]["uri"], "search"

    return None, "unmatched"


def create_playlist(token: str, name: str, description: str, public: bool) -> dict[str, Any]:
    body = {"name": name, "description": description, "public": public}
    return _request(token, "POST", "/users/me/playlists", body)


def add_tracks(token: str, playlist_id: str, uris: list[str]) -> None:
    """Spotify caps add-tracks at 100 URIs per call; we batch."""
    for i in range(0, len(uris), 100):
        chunk = uris[i : i + 100]
        _request(token, "POST", f"/playlists/{playlist_id}/tracks", {"uris": chunk})
