"""Tests for the streaming integrations: services capability flag, preview
endpoint, Spotify track resolution, Apple developer JWT signing, Spotify
PKCE state machine, and the Spotify export endpoint with the API stubbed."""

from __future__ import annotations

import base64
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from setplot.server.app import create_app


# ---------------------------------------------------------------------------
# /api/sets/{id} services capability flag
# ---------------------------------------------------------------------------
def test_services_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("APPLE_MUSIC_TEAM_ID", raising=False)
    from setplot import store
    from setplot.pipeline import ingest as ingest_mod

    from .conftest import FIXTURES
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    store.init_status(sid, root=tmp_path)
    client = TestClient(create_app())
    body = client.get(f"/api/sets/{sid}").json()
    assert body["services"]["spotify"]["enabled"] is False
    assert body["services"]["apple"]["enabled"] is False


def test_services_spotify_enabled_when_client_id_set(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "abc123")
    from setplot import store
    from setplot.pipeline import ingest as ingest_mod

    from .conftest import FIXTURES
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    store.init_status(sid, root=tmp_path)
    client = TestClient(create_app())
    body = client.get(f"/api/sets/{sid}").json()
    assert body["services"]["spotify"]["enabled"] is True
    assert body["services"]["apple"]["enabled"] is False


# ---------------------------------------------------------------------------
# /api/preview — fall through if external lookup fails (offline-friendly).
# Real network call is exercised manually; here we stub the lookup.
# ---------------------------------------------------------------------------
def test_preview_apple_returns_url(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    fake_payload = {
        "preview_url": "https://x/y.m4a",
        "artwork_url": "https://x/art.jpg",
        "track_name": "T",
        "artist_name": "A",
    }
    with patch("setplot.services.apple_lookup.lookup", return_value=fake_payload):
        client = TestClient(create_app())
        r = client.get("/api/preview", params={"service": "apple", "id": "12345"})
    assert r.status_code == 200
    assert r.json()["preview_url"].endswith(".m4a")


def test_preview_apple_404_when_no_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    with patch("setplot.services.apple_lookup.lookup", return_value=None):
        client = TestClient(create_app())
        r = client.get("/api/preview", params={"service": "apple", "id": "999"})
    assert r.status_code == 404


def test_preview_unknown_service_400(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())
    r = client.get("/api/preview", params={"service": "tidal", "id": "1"})
    assert r.status_code == 422  # FastAPI Query pattern validator rejects


# ---------------------------------------------------------------------------
# Spotify auth: PKCE state machine — invalid state must fail loudly.
# ---------------------------------------------------------------------------
def test_spotify_login_redirects_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fake-client")
    client = TestClient(create_app())
    r = client.get("/auth/spotify/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.spotify.com/authorize")
    assert "code_challenge_method=S256" in loc
    assert "client_id=fake-client" in loc


def test_spotify_login_503_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    client = TestClient(create_app())
    r = client.get("/auth/spotify/login", follow_redirects=False)
    assert r.status_code == 503


def test_spotify_callback_unknown_state_returns_500(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fake-client")
    client = TestClient(create_app())
    r = client.get("/auth/spotify/callback?code=abc&state=never_issued")
    # We render an HTML page on failure, not a JSON 4xx — verify the page text.
    assert r.status_code == 500
    assert "Token exchange failed" in r.text


# ---------------------------------------------------------------------------
# Spotify track resolution
# ---------------------------------------------------------------------------
def test_resolve_track_uses_existing_acr_id():
    from setplot.services import spotify as sp
    uri, reason = sp.resolve_track("token-not-used", {"spotify": "abc123"})
    assert uri == "spotify:track:abc123"
    assert reason == "acr_id"


def test_resolve_track_falls_back_to_isrc():
    from setplot.services import spotify as sp
    fake_response = {"tracks": {"items": [{"uri": "spotify:track:from-isrc"}]}}
    with patch("setplot.services.spotify._safe_request", return_value=fake_response) as m:
        uri, reason = sp.resolve_track("tok", {"isrc": "USRC12345678"})
    assert uri == "spotify:track:from-isrc"
    assert reason == "isrc"
    # Verify we used the ISRC search syntax in the URL.
    assert "isrc" in m.call_args[0][2].lower()


def test_resolve_track_text_search_requires_artist_match():
    from setplot.services import spotify as sp
    fake_response = {
        "tracks": {
            "items": [
                {"uri": "spotify:track:wrong", "artists": [{"name": "Some Other Band"}]},
                {"uri": "spotify:track:right", "artists": [{"name": "Aphex Twin"}]},
            ]
        }
    }
    with patch("setplot.services.spotify._safe_request", return_value=fake_response):
        uri, reason = sp.resolve_track("tok", {"title": "Windowlicker", "artists": "Aphex Twin"})
    assert uri == "spotify:track:right"
    assert reason == "search"


def test_resolve_track_unmatched_when_no_data():
    from setplot.services import spotify as sp
    uri, reason = sp.resolve_track("tok", {})
    assert uri is None
    assert reason == "unmatched"


# ---------------------------------------------------------------------------
# Apple developer JWT signing — generate a key, sign, verify shape + signature.
# ---------------------------------------------------------------------------
@pytest.fixture
def apple_key(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "AuthKey.p8"
    p.write_bytes(pem)
    monkeypatch.setenv("APPLE_MUSIC_TEAM_ID", "TEAMID0000")
    monkeypatch.setenv("APPLE_MUSIC_KEY_ID", "KEYID00000")
    monkeypatch.setenv("APPLE_MUSIC_KEY_PATH", str(p))
    # Reset the in-memory cache between tests since it's module-level.
    from setplot.server.auth import apple
    apple._token_cache["token"] = None
    apple._token_cache["expires_at"] = 0
    return key


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_apple_developer_token_signature_verifies(apple_key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from setplot.server.auth import apple as apple_auth
    tok = apple_auth.developer_token()
    h_b64, p_b64, s_b64 = tok.split(".")
    header = json.loads(_b64url_decode(h_b64))
    claims = json.loads(_b64url_decode(p_b64))
    assert header == {"alg": "ES256", "kid": "KEYID00000"}
    assert claims["iss"] == "TEAMID0000"
    assert claims["exp"] > claims["iat"]
    sig = _b64url_decode(s_b64)
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    apple_key.public_key().verify(
        encode_dss_signature(r, s),
        f"{h_b64}.{p_b64}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


def test_apple_dev_token_endpoint_503_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    for k in ("APPLE_MUSIC_TEAM_ID", "APPLE_MUSIC_KEY_ID", "APPLE_MUSIC_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    client = TestClient(create_app())
    r = client.get("/api/apple/dev-token")
    assert r.status_code == 503


def test_apple_dev_token_endpoint_returns_jwt_when_configured(tmp_path, apple_key, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())
    r = client.get("/api/apple/dev-token")
    assert r.status_code == 200
    assert r.json()["token"].count(".") == 2  # JWT structure


# ---------------------------------------------------------------------------
# Token store: load/save/refresh roundtrip + 0600 perms
# ---------------------------------------------------------------------------
def test_token_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_TOKEN_DIR", str(tmp_path))
    from setplot.server import auth as ts
    ts.save("fake_service", {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
    loaded = ts.load("fake_service")
    assert loaded["access_token"] == "AT"
    assert loaded["refresh_token"] == "RT"
    assert "stored_at" in loaded
    # Permissions check skipped on Windows where chmod is a no-op.
    if hasattr(os, "stat") and not os.name == "nt":
        st = (tmp_path / "fake_service_token.json").stat()
        assert oct(st.st_mode & 0o777) == "0o600"


def test_token_store_is_expired():
    import time

    from setplot.server import auth as ts
    expired = {"stored_at": int(time.time()) - 3700, "expires_in": 3600}
    fresh = {"stored_at": int(time.time()) - 60, "expires_in": 3600}
    assert ts.is_expired(expired)
    assert not ts.is_expired(fresh)


# ---------------------------------------------------------------------------
# /api/export/spotify end-to-end with stubbed Spotify API
# ---------------------------------------------------------------------------
def test_export_spotify_full_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SETPLOT_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fake-client")

    from setplot import store
    from setplot.pipeline import ingest as ingest_mod
    from setplot.server import auth as ts

    from .conftest import FIXTURES
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    # Synthesize a tracks.json with a recognized track that has a Spotify ID.
    tracks_doc = {
        "stride_s": 30,
        "rec_length_s": 10,
        "merged": [
            {"start": 0.0, "title": "Test", "artists": "Artist", "score": 100, "spotify": "abc123"},
            {"start": 30.0, "title": "Other", "artists": "X", "score": 90, "isrc": "USRC12345678"},
        ],
        "windows": {},
    }
    tracks_path = store.step_output_path(sid, "fingerprint", root=tmp_path)
    tracks_path.write_text(json.dumps(tracks_doc))

    # Pretend we already authorized.
    ts.save("spotify", {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_in": 3600,
    })

    from setplot.services import spotify as sp_api
    created_playlist = {"id": "playlist123", "external_urls": {"spotify": "https://open.spotify.com/playlist/playlist123"}}

    def fake_request(token, method, path, body=None):
        if method == "POST" and path == "/users/me/playlists":
            return created_playlist
        if method == "POST" and path.endswith("/tracks"):
            return {}
        return {}

    isrc_response = {"tracks": {"items": [{"uri": "spotify:track:from-isrc"}]}}

    with patch.object(sp_api, "_request", side_effect=fake_request), \
         patch.object(sp_api, "_safe_request", return_value=isrc_response):
        client = TestClient(create_app())
        r = client.post("/api/export/spotify", json={"set_id": sid, "public": False})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] == 2  # both resolved (one via acr_id, one via isrc)
    assert body["playlist_url"].endswith("/playlist123")
    assert body["resolution"]["acr_id"] == 1
    assert body["resolution"]["isrc"] == 1


def test_export_spotify_401_when_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SETPLOT_TOKEN_DIR", str(tmp_path / "tokens"))
    from setplot import store
    from setplot.pipeline import ingest as ingest_mod

    from .conftest import FIXTURES
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    tracks_path = store.step_output_path(sid, "fingerprint", root=tmp_path)
    tracks_path.write_text(json.dumps({"merged": [{"title": "x", "artists": "y", "score": 50}]}))

    client = TestClient(create_app())
    r = client.post("/api/export/spotify", json={"set_id": sid})
    assert r.status_code == 401


def test_export_spotify_409_when_no_fingerprint_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SETPLOT_TOKEN_DIR", str(tmp_path / "tokens"))
    from setplot.pipeline import ingest as ingest_mod
    from setplot.server import auth as ts

    from .conftest import FIXTURES
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    ts.save("spotify", {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})

    client = TestClient(create_app())
    r = client.post("/api/export/spotify", json={"set_id": sid})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# /api/export/tracks/{set_id} — feed for the apple-export.js client flow
# ---------------------------------------------------------------------------
def test_export_tracks_dedups_and_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    from setplot import store
    from setplot.pipeline import ingest as ingest_mod

    from .conftest import FIXTURES
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    doc = {
        "merged": [
            {"title": "A", "artists": "X", "score": 90, "apple": "1"},
            # duplicate (case-insensitive title+artist+isrc) → drop
            {"title": "a", "artists": "X", "score": 90, "isrc": ""},
            {"title": "B", "artists": "Y", "score": 30, "apple": "2"},  # below threshold
        ],
    }
    store.step_output_path(sid, "fingerprint", root=tmp_path).write_text(json.dumps(doc))
    client = TestClient(create_app())
    r = client.get(f"/api/export/tracks/{sid}", params={"min_score": 50})
    assert r.status_code == 200
    body = r.json()
    titles = [t["title"] for t in body["tracks"]]
    assert titles == ["A"]  # both dups collapsed, low-score filtered
