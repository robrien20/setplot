"""Drive a single set through ingest → peaks → bpm → key → fingerprint.

Each step writes its own JSON output into the set's data directory and updates
``status.json`` so that the Phase 3 viewer can render skeleton state for
in-flight steps and final state for completed ones.

Fan-out of failures: if a step raises, we mark it ``failed: <msg>`` in
status.json and re-raise. Subsequent steps are NOT skipped — the orchestrator
continues so that a transient failure in fingerprint (e.g. ACR creds missing)
doesn't tank the BPM and key data the user does have.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from setplot import store
from setplot.pipeline import bpm as bpm_mod
from setplot.pipeline import fingerprint as fp_mod
from setplot.pipeline import key as key_mod
from setplot.pipeline import peaks as peaks_mod

EventCb = Callable[[dict[str, Any]], None] | None
"""Hook invoked for {step, state, [data]} events. Phase 3 plugs SSE into this."""


def _emit(cb: EventCb, payload: dict[str, Any]) -> None:
    if cb is not None:
        cb(payload)


# ---------------------------------------------------------------------------
# Step runners — each returns the artefact path it produced, or raises.
# Each ALSO writes the canonical step JSON the viewer will consume.
# ---------------------------------------------------------------------------
def _run_peaks(set_id: str, root: Path | None) -> Path:
    return peaks_mod.make_peaks(set_id, root=root)


def _run_bpm(set_id: str, root: Path | None, step: float, window: float, chunk_min: float) -> Path:
    src = store.find_source(set_id, root=root)
    if src is None:
        raise FileNotFoundError(f"no source media in set {set_id}")
    pairs = bpm_mod.scan_file(src, step, window, chunk_min, sr=22050, start_bpm=130.0)
    out = store.step_output_path(set_id, "bpm", root=root)
    out.write_text(
        json.dumps(
            {"step_s": step, "window_s": window, "data": [[t, b] for t, b in pairs]},
            ensure_ascii=False,
        )
    )
    return out


def _run_key(
    set_id: str,
    root: Path | None,
    step: float,
    window: float,
    chunk_min: float,
    engine: str,
) -> Path:
    src = store.find_source(set_id, root=root)
    if src is None:
        raise FileNotFoundError(f"no source media in set {set_id}")

    if engine == "essentia" and not key_mod.HAS_ESSENTIA:
        engine = "librosa"
    if engine == "essentia":
        import librosa as _librosa

        duration = _librosa.get_duration(path=str(src))
        rows = key_mod.scan_essentia(src, step, window, duration)
    else:
        rows = key_mod.scan_librosa(src, step, window, chunk_min, sr=22050)

    payload_rows = [
        [t, key_mod.pc_mode_to_camelot(pc, mode), key_mod.pc_mode_to_name(pc, mode), strength, margin]
        for (t, pc, mode, strength, margin) in rows
    ]
    out = store.step_output_path(set_id, "key", root=root)
    out.write_text(
        json.dumps(
            {"step_s": step, "window_s": window, "engine": engine, "data": payload_rows},
            ensure_ascii=False,
        )
    )
    return out


def _hit_to_window_candidate(h: fp_mod.Hit) -> dict[str, Any]:
    return {
        "score": h.score,
        "title": h.title,
        "artists": h.artists,
        "album": h.album,
        "label": h.label,
        "isrc": h.isrc,
        "play_offset_ms": h.play_offset_ms,
        "duration_ms": h.duration_ms,
        "result_from": h.result_from,
        "spotify": h.spotify_id,
        "deezer": h.deezer_id,
        "youtube": h.youtube_vid,
        "musicbrainz": h.musicbrainz_id,
        "apple": h.apple_id,
    }


def _merged_to_viewer_shape(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate ``dedupe_and_merge`` output into the shape the viewer wants
    (``start`` / ``hits`` / ``score`` / shortened external-id fields)."""
    out: list[dict[str, Any]] = []
    for m in merged:
        out.append(
            {
                "start": m["estimated_track_start_s"],
                "first": m["first_seen_s"],
                "last": m["last_seen_s"],
                "hits": m["hit_count"],
                "score": m["best_score"],
                "title": m["title"],
                "artists": m["artists"],
                "album": m.get("album", ""),
                "label": m.get("label", ""),
                "release_date": m.get("release_date", ""),
                "genres": m.get("genres", ""),
                "duration_ms": m.get("duration_ms", 0),
                "isrc": m.get("isrc", ""),
                "spotify": m.get("spotify_id", ""),
                "deezer": m.get("deezer_id", ""),
                "youtube": m.get("youtube_vid", ""),
                "musicbrainz": m.get("musicbrainz_id", ""),
                "apple": m.get("apple_id", ""),
                "spotify_url": m.get("spotify_url", ""),
                "youtube_url": m.get("youtube_url", ""),
            }
        )
    return out


def _run_fingerprint(set_id: str, root: Path | None, stride: float, rec_length: int) -> Path:
    src = store.find_source(set_id, root=root)
    if src is None:
        raise FileNotFoundError(f"no source media in set {set_id}")

    host = os.environ.get("ACR_HOST")
    key = os.environ.get("ACR_ACCESS_KEY")
    secret = os.environ.get("ACR_ACCESS_SECRET")
    if not (host and key and secret):
        raise RuntimeError(
            "ACR creds missing — set ACR_HOST / ACR_ACCESS_KEY / ACR_ACCESS_SECRET to enable fingerprinting."
        )

    from acrcloud.recognizer import ACRCloudRecognizer

    recognizer = ACRCloudRecognizer({"host": host, "access_key": key, "access_secret": secret, "timeout": 15})
    duration = fp_mod.probe_duration(src)
    hits, _observed = fp_mod.scan_file(
        recognizer, src, duration, stride, rec_length, 0.0, float("inf"), audd_token=None
    )
    merged = fp_mod.dedupe_and_merge(hits)

    # Build per-window candidates dict keyed by window_start_s (as a string for JSON).
    windows: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        bucket = windows.setdefault(str(int(h.window_start_s)), [])
        bucket.append(_hit_to_window_candidate(h))
    # Sort each window's candidates by descending score.
    for cands in windows.values():
        cands.sort(key=lambda c: -c["score"])

    out = store.step_output_path(set_id, "fingerprint", root=root)
    out.write_text(
        json.dumps(
            {
                "stride_s": stride,
                "rec_length_s": rec_length,
                "merged": _merged_to_viewer_shape(merged),
                "windows": windows,
            },
            ensure_ascii=False,
        )
    )
    return out


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------
def analyze(
    set_id: str,
    *,
    root: Path | None = None,
    bpm_step: float = 5.0,
    bpm_window: float = 24.0,
    key_step: float = 10.0,
    key_window: float = 24.0,
    key_engine: str = "essentia",
    fingerprint_stride: float = 30.0,
    fingerprint_rec_length: int = 10,
    chunk_min: float = 10.0,
    skip: tuple[str, ...] = (),
    event_cb: EventCb = None,
) -> dict[str, str]:
    """Run peaks → bpm → key → fingerprint for an already-ingested set.

    Returns the final ``steps`` dict from status.json. Raises only on programmer
    error (missing set dir / unknown skip key); per-step failures are recorded
    in status and the next step still runs.
    """
    if not store.set_dir(set_id, root=root).exists():
        raise FileNotFoundError(f"set {set_id} not found")
    for s in skip:
        if s not in store.STEPS:
            raise ValueError(f"unknown step in skip: {s!r}")

    runners: dict[str, Callable[[], Path]] = {
        "peaks": lambda: _run_peaks(set_id, root),
        "bpm": lambda: _run_bpm(set_id, root, bpm_step, bpm_window, chunk_min),
        "key": lambda: _run_key(set_id, root, key_step, key_window, chunk_min, key_engine),
        "fingerprint": lambda: _run_fingerprint(set_id, root, fingerprint_stride, fingerprint_rec_length),
    }
    for step_name, runner in runners.items():
        if step_name in skip:
            store.update_step(set_id, step_name, "skipped", root=root)
            _emit(event_cb, {"step": step_name, "state": "skipped"})
            continue
        store.update_step(set_id, step_name, "running", root=root)
        _emit(event_cb, {"step": step_name, "state": "running"})
        try:
            runner()
            store.update_step(set_id, step_name, "done", root=root)
            _emit(event_cb, {"step": step_name, "state": "done"})
        except Exception as exc:
            store.update_step(set_id, step_name, f"failed: {exc}", root=root)
            _emit(event_cb, {"step": step_name, "state": "failed", "error": str(exc)})

    return store.read_status(set_id, root=root)["steps"]
