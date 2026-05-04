"""OAuth callback + dev-token endpoints for streaming integrations.

* ``GET  /auth/spotify/login``       → 302 to Spotify's authorize URL
* ``GET  /auth/spotify/callback``    → exchanges code for tokens, redirects home
* ``POST /auth/spotify/disconnect``  → wipes the local token file
* ``GET  /api/auth/status``          → ``{spotify: {connected: bool}, apple: {...}}``
* ``GET  /api/apple/dev-token``      → MusicKit JS developer JWT (when configured)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from setplot.config import get_settings
from setplot.server import auth as token_store
from setplot.server.auth import spotify as spotify_auth

router = APIRouter(tags=["auth"])


@router.get("/auth/spotify/login")
async def spotify_login() -> RedirectResponse:
    settings = get_settings()
    if not settings.spotify_enabled():
        raise HTTPException(status_code=503, detail="spotify integration not configured")
    return RedirectResponse(url=spotify_auth.begin())


@router.get("/auth/spotify/callback", response_class=HTMLResponse)
async def spotify_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
) -> HTMLResponse:
    """Exchange the auth code, then close this window. The browser tab that
    initiated /auth/spotify/login already moved on; this is the popup/redirect
    target so we just present a simple "you can close this" page."""
    if error:
        return HTMLResponse(_close_html(f"Spotify auth error: {error}"))
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")
    try:
        spotify_auth.exchange(code, state)
    except Exception as exc:
        return HTMLResponse(_close_html(f"Token exchange failed: {exc}"), status_code=500)
    return HTMLResponse(_close_html("Connected to Spotify. You can close this tab."))


@router.post("/auth/spotify/disconnect")
async def spotify_disconnect() -> dict[str, bool]:
    token_store.clear("spotify")
    return {"ok": True}


@router.get("/api/auth/status")
async def auth_status() -> dict[str, dict[str, bool]]:
    sp = token_store.load("spotify")
    return {
        "spotify": {"connected": bool(sp and sp.get("access_token"))},
        "apple": {"connected": False},  # Apple connect state lives in MusicKit JS in-browser
    }


@router.get("/api/apple/dev-token")
async def apple_dev_token() -> dict[str, str]:
    settings = get_settings()
    if not settings.apple_music_enabled():
        raise HTTPException(status_code=503, detail="apple music integration not configured")
    from setplot.server.auth import apple as apple_auth  # lazy: pulls in cryptography
    token = apple_auth.developer_token()
    return {"token": token}


def _close_html(message: str) -> str:
    return (
        "<!doctype html><html><body style='font-family:system-ui;padding:32px;background:#0e1420;color:#e6ecf5;'>"
        f"<p>{message}</p>"
        "<p><a href='/' style='color:#4dd0e1;'>back to library</a></p>"
        "<script>setTimeout(() => window.close(), 1500);</script>"
        "</body></html>"
    )
