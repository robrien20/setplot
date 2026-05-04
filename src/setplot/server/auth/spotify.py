"""Spotify OAuth (Authorization Code with PKCE) and token refresh.

The full PKCE flow:

1. ``begin()`` mints a code verifier + S256 challenge, stashes the verifier
   keyed by ``state``, and returns the URL the user should visit on
   accounts.spotify.com.
2. The user authorises and Spotify redirects to our callback with ``state``
   and ``code``.
3. ``exchange()`` POSTs to ``/api/token`` with the code and the stored
   verifier; on success the token bundle is persisted to disk via
   ``setplot.server.auth.save``.
4. ``refresh()`` exchanges the refresh_token for a fresh access_token when
   ``is_expired`` flags one stale.

We never need a client_secret — that's the whole point of PKCE.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.parse
import urllib.request
from typing import Any

from setplot.config import get_settings
from setplot.server import auth as token_store

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
DEFAULT_SCOPES = "playlist-modify-private playlist-modify-public"

# In-memory state map (state → code_verifier). Single-user, single-process —
# we don't need this to survive a restart since the auth flow is short-lived.
_pending: dict[str, str] = {}
_pending_lock = threading.Lock()


def _b64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _make_verifier_challenge() -> tuple[str, str]:
    verifier = _b64url_no_pad(secrets.token_bytes(64))  # 86-char URL-safe string
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def begin(scopes: str = DEFAULT_SCOPES) -> str:
    """Return the Spotify authorize URL for the user to open. Stashes verifier
    server-side keyed by a fresh ``state`` value."""
    settings = get_settings()
    if not settings.spotify_client_id:
        raise RuntimeError("SPOTIFY_CLIENT_ID is not configured")
    state = secrets.token_urlsafe(16)
    verifier, challenge = _make_verifier_challenge()
    with _pending_lock:
        _pending[state] = verifier
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": scopes,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange(code: str, state: str) -> dict[str, Any]:
    """Exchange the auth code for an access + refresh token; persist."""
    with _pending_lock:
        verifier = _pending.pop(state, None)
    if not verifier:
        raise RuntimeError("unknown/expired oauth state — restart the auth flow")
    settings = get_settings()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.spotify_redirect_uri,
        "client_id": settings.spotify_client_id,
        "code_verifier": verifier,
    }).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read())
    token_store.save("spotify", payload)
    return payload


def refresh(refresh_token: str) -> dict[str, Any]:
    """Use a stored refresh_token to obtain a fresh access_token. Spotify may
    or may not return a new refresh_token; if not, we keep the old one."""
    settings = get_settings()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.spotify_client_id,
    }).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read())
    if "refresh_token" not in payload:
        payload["refresh_token"] = refresh_token
    token_store.save("spotify", payload)
    return payload


def get_valid_access_token() -> str | None:
    """Load the on-disk token, refreshing if expired. Returns None if no
    user has connected yet, or if the refresh failed."""
    bundle = token_store.load("spotify")
    if not bundle:
        return None
    if token_store.is_expired(bundle):
        rt = bundle.get("refresh_token")
        if not rt:
            return None
        try:
            bundle = refresh(rt)
        except Exception:
            return None
    return bundle.get("access_token")
