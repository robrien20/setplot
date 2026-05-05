"""Library endpoints: list all known sets + return one set's metadata + status.

Per-step JSONs (bpm.json, key.json, tracks.json, peaks.json) are exposed via
the media router so each can be fetched independently — the viewer benefits
from being able to load BPM first and key/tracks lazily for big sets.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from setplot import store
from setplot.config import get_settings

router = APIRouter(tags=["library"])


def _services_capability() -> dict[str, dict[str, bool]]:
    """Tell the viewer which streaming integrations have credentials configured.
    The frontend hides export buttons whose service flag is False."""
    s = get_settings()
    return {
        "spotify": {"enabled": s.spotify_enabled()},
        "apple": {"enabled": s.apple_music_enabled()},
    }


def _summarise(meta: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    """Flatten the on-disk metadata + status into a viewer-friendly card payload."""
    steps = status.get("steps", {})
    return {
        "set_id": meta.get("set_id"),
        "title": meta.get("title", "?"),
        "duration_s": meta.get("duration_s"),
        "uploader": meta.get("uploader"),
        "source_url": meta.get("source_url"),
        "ingested_at": meta.get("ingested_at"),
        "video_id": meta.get("video_id"),
        "steps": steps,
        "completed_steps": sum(1 for v in steps.values() if v == "done"),
        "total_steps": len(steps),
        "analysis_version": status.get("analysis_version"),
    }


@router.get("/sets")
async def list_sets() -> dict[str, Any]:
    rows = store.list_sets()
    return {"sets": [_summarise(r["metadata"], r["status"]) for r in rows]}


@router.get("/sets/{set_id}")
async def get_set(set_id: str) -> dict[str, Any]:
    if not store.set_dir(set_id).exists():
        raise HTTPException(status_code=404, detail=f"set {set_id} not found")
    meta = store.read_metadata(set_id)
    try:
        status = store.read_status(set_id)
    except FileNotFoundError:
        status = {"analysis_version": store.ANALYSIS_VERSION, "steps": dict.fromkeys(store.STEPS, "pending")}
    return {**_summarise(meta, status), "services": _services_capability()}
