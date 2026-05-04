"""GET /api/preview — return a 30-second preview URL for a track.

Apple Music previews come from the public iTunes Search lookup endpoint
(no auth, cached on disk). Spotify previews are handled in the browser via
the open.spotify.com/embed iframe widget — we don't proxy them here.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from setplot.config import get_settings
from setplot.services import apple_lookup

router = APIRouter(tags=["preview"])


@router.get("/preview")
async def preview(
    service: str = Query(..., pattern="^(apple)$"),
    id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, str]:
    """Currently only ``service=apple`` is supported. Spotify embeds handle
    themselves client-side."""
    settings = get_settings()
    if service == "apple":
        info = apple_lookup.lookup(id, cache_dir=settings.data_dir())
        if not info or not info.get("preview_url"):
            raise HTTPException(status_code=404, detail="no preview available")
        return info
    raise HTTPException(status_code=400, detail=f"unknown service {service!r}")
