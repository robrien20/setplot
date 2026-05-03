"""FastAPI app factory.

Mounts the viewer's static assets at ``/`` and the API at ``/api``. The viewer
is served as plain static HTML/CSS/JS; the API is JSON + SSE + range-supported
media. Single-origin deployment, so no CORS gymnastics.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import setplot
from setplot import __version__
from setplot.server.events import bus
from setplot.server.routers import ingest, library, media

VIEWER_DIR = Path(setplot.__file__).parent / "viewer"


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

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse(url="/index.html")

    # Mount viewer last so /api/* routes win.
    app.mount("/", StaticFiles(directory=VIEWER_DIR, html=False), name="viewer")
    return app
