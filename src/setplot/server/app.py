"""FastAPI app factory.

Mounts the viewer's static assets at ``/`` and the API at ``/api``. The viewer
is served as plain static HTML/CSS/JS; the API is JSON + SSE + range-supported
media. Single-origin deployment, so no CORS gymnastics.
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import setplot
from setplot import __version__
from setplot.server.events import bus
from setplot.server.routers import auth, export, ingest, library, media, preview

VIEWER_DIR = Path(setplot.__file__).parent / "viewer"

# 3-bar audio-meter glyph on dark blue. SVG so it scales for retina without
# shipping an .ico binary; browsers accept image/svg+xml at /favicon.ico.
_FAVICON_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
    b"<rect width='16' height='16' rx='3' fill='#0a2a5a'/>"
    b"<rect x='3' y='6' width='2' height='4' fill='#fff'/>"
    b"<rect x='7' y='3' width='2' height='10' fill='#fff'/>"
    b"<rect x='11' y='7' width='2' height='2' fill='#fff'/>"
    b"</svg>"
)


def _make_apple_touch_icon(size: int = 180) -> bytes:
    """Render the favicon glyph as a PNG for iOS home-screen icons.

    iOS Safari probes /apple-touch-icon{,-precomposed}.png and won't accept
    SVG, so we synthesize a PNG at import time using stdlib only — no Pillow
    dep, no binary asset on disk.
    """
    bg = b"\x0a\x2a\x5a"
    fg = b"\xff\xff\xff"
    scale = size / 16
    # (x0, y0, x1, y1) bars in the 16x16 grid, scaled up.
    bars = [
        (int(3 * scale), int(6 * scale), int(5 * scale), int(10 * scale)),
        (int(7 * scale), int(3 * scale), int(9 * scale), int(13 * scale)),
        (int(11 * scale), int(7 * scale), int(13 * scale), int(9 * scale)),
    ]
    bg_row = bg * size
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # PNG filter byte: None
        row = bytearray(bg_row)
        for x0, y0, x1, y1 in bars:
            if y0 <= y < y1:
                row[x0 * 3 : x1 * 3] = fg * (x1 - x0)
        raw.extend(row)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB, no interlace
    idat = zlib.compress(bytes(raw), 9)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


_APPLE_TOUCH_ICON_PNG = _make_apple_touch_icon()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Capture the running loop so the JobBus can post from worker threads."""
    bus.attach_loop(asyncio.get_running_loop())
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="SetPlot",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    app.include_router(library.router, prefix="/api")
    app.include_router(media.router, prefix="/api")
    app.include_router(ingest.router, prefix="/api")
    app.include_router(preview.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    # auth router defines both /auth/* (browser-facing) and /api/auth/* routes itself.
    app.include_router(auth.router)

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse(url="/index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def _favicon() -> Response:
        return Response(
            content=_FAVICON_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    @app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
    async def _apple_touch_icon() -> Response:
        return Response(
            content=_APPLE_TOUCH_ICON_PNG,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Mount viewer last so /api/* routes win.
    app.mount("/", StaticFiles(directory=VIEWER_DIR, html=False), name="viewer")
    return app
