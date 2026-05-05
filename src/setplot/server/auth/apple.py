"""Apple Music developer JWT signing for MusicKit JS.

The token is an ES256-signed JWT identifying the *developer* (i.e. the SetPlot
operator's Apple Developer team). The browser uses it to bootstrap MusicKit JS;
the user music token (which authorises library writes) is fetched separately
by MusicKit JS itself when the user signs in to Apple ID.

Apple caps the validity at 6 months; we sign for 24h and cache in-memory so we
don't reload the .p8 key for every page load.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from setplot.config import get_settings

_TOKEN_TTL_S = 24 * 60 * 60
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0}
_lock = threading.Lock()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _ecdsa_sig_to_jws(der_sig: bytes) -> bytes:
    """JWS encodes ECDSA signatures as the raw concatenation of (r, s),
    each padded to 32 bytes for P-256. ``cryptography`` returns DER, so we
    decode and re-encode."""
    r, s = decode_dss_signature(der_sig)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def developer_token() -> str:
    """Return a cached signed developer JWT, regenerating after TTL."""
    now = int(time.time())
    with _lock:
        if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
            return _token_cache["token"]

        settings = get_settings()
        if not settings.apple_music_enabled():
            raise RuntimeError("apple music integration not configured")

        team_id = settings.apple_music_team_id
        key_id = settings.apple_music_key_id
        key_path = settings.apple_music_key_path

        pem = key_path.read_bytes()
        key = load_pem_private_key(pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise RuntimeError("apple music key must be EC P-256")

        header = {"alg": "ES256", "kid": key_id}
        claims = {"iss": team_id, "iat": now, "exp": now + _TOKEN_TTL_S}
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")) + "."
            + _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        ).encode("ascii")
        sig_der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        jwt = signing_input.decode("ascii") + "." + _b64url(_ecdsa_sig_to_jws(sig_der))

        _token_cache["token"] = jwt
        _token_cache["expires_at"] = now + _TOKEN_TTL_S
        return jwt
