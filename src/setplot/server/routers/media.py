"""Per-set media: audio with HTTP Range, thumbnail, per-step JSON pass-throughs.

Audio is served with byte-range support so the browser's ``<audio>`` element
can seek a multi-hour file without downloading the whole thing first.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from setplot import store

router = APIRouter(tags=["media"])

# RFC 7233 — `Range: bytes=START-END` (END optional).
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")
_AUDIO_CHUNK = 1024 * 1024  # 1 MiB per yield — keeps memory bounded for 7h files


def _ext_to_content_type(ext: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
        ".flac": "audio/flac",
        ".wav": "audio/wav",
    }.get(ext.lower(), "application/octet-stream")


def _ranged_iter(path: Path, start: int, end_inclusive: int) -> Iterable[bytes]:
    """Yield ``[start, end_inclusive]`` from ``path`` in 1 MiB chunks."""
    remaining = end_inclusive - start + 1
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(_AUDIO_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/sets/{set_id}/audio")
async def audio(set_id: str, request: Request) -> Response:
    src = store.find_source(set_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"no source media in set {set_id}")

    file_size = src.stat().st_size
    media_type = _ext_to_content_type(src.suffix)

    range_hdr = request.headers.get("range")
    if range_hdr is None:
        # No Range — serve the whole file. FileResponse already handles this efficiently.
        return FileResponse(src, media_type=media_type, headers={"Accept-Ranges": "bytes"})

    m = _RANGE_RE.match(range_hdr)
    if not m:
        # Malformed Range header → 416 Range Not Satisfiable.
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else file_size - 1
    if start > end or end >= file_size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Type": media_type,
    }
    return StreamingResponse(
        _ranged_iter(src, start, end), status_code=206, headers=headers, media_type=media_type
    )


@router.get("/sets/{set_id}/thumbnail")
async def thumbnail(set_id: str) -> FileResponse:
    p = store.set_dir(set_id) / "thumbnail.jpg"
    if not p.exists():
        raise HTTPException(status_code=404, detail="no thumbnail")
    return FileResponse(p, media_type="image/jpeg")


def _passthrough_json(set_id: str, name: str) -> Response:
    p = store.set_dir(set_id) / name
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"{name} not generated yet")
    # Stream the bytes as-is — no parse-then-reserialise round trip.
    return Response(content=p.read_bytes(), media_type="application/json")


@router.get("/sets/{set_id}/peaks.json")
async def peaks_json(set_id: str) -> Response:
    return _passthrough_json(set_id, "peaks.json")


@router.get("/sets/{set_id}/bpm.json")
async def bpm_json(set_id: str) -> Response:
    return _passthrough_json(set_id, "bpm.json")


@router.get("/sets/{set_id}/key.json")
async def key_json(set_id: str) -> Response:
    return _passthrough_json(set_id, "key.json")


@router.get("/sets/{set_id}/tracks.json")
async def tracks_json(set_id: str) -> Response:
    return _passthrough_json(set_id, "tracks.json")


@router.get("/sets/{set_id}/status.json")
async def status_json(set_id: str) -> JSONResponse:
    try:
        return JSONResponse(store.read_status(set_id))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
