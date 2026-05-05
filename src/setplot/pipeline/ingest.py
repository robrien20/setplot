"""Ingest step: pull a URL via yt-dlp, or import a local file, into a per-set dir.

Both paths emit the same artefacts under ``data/{set_id}/``:

- ``source.<ext>``        -- the audio/video file we'll analyse
- ``thumbnail.jpg``       -- present for URL ingests; not for local files
- ``metadata.json``       -- title / source_url / uploader / duration_s / ingested_at / set_id
- ``status.json``         -- initialised with all steps "pending"; ``ingest`` is set to "done"

For URL ingest we use yt-dlp's Python API directly (not subprocess). Format
selector prefers 720p video + AAC audio in an MP4 container (YouTube), falling
back to best ≤720p single stream, then to audio-only sources (SoundCloud,
Bandcamp, etc.) where no video track exists.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from setplot import store

# 720p video + m4a audio merged to mp4; fall back to a single ≤720p stream;
# fall back to best audio for audio-only sources (SoundCloud); finally, best
# of anything yt-dlp can reach.
URL_FORMAT = "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]/ba/b"


# ---------------------------------------------------------------------------
# Local file
# ---------------------------------------------------------------------------
def _probe_duration_seconds(path: Path) -> float | None:
    """Best-effort duration via ffprobe. Returns None on failure (caller proceeds anyway)."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            text=True,
        ).strip()
        return float(out) if out else None
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def ingest_local(path: Path, *, root: Path | None = None) -> str:
    """Copy a local media file into a new per-set dir. Returns set_id."""
    path = Path(path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    title = path.stem
    set_id = store.make_set_id(title, str(path))
    d = store.set_dir(set_id, root=root)
    d.mkdir(parents=True, exist_ok=True)

    dest = d / f"source{path.suffix.lower()}"
    if not dest.exists() or dest.stat().st_size != path.stat().st_size:
        shutil.copy2(path, dest)

    duration = _probe_duration_seconds(dest)
    meta: dict[str, Any] = {
        "title": title,
        "source_url": path.as_uri(),
        "uploader": None,
        "duration_s": duration,
    }
    store.write_metadata(set_id, meta, root=root)
    status = store.init_status(set_id, root=root)
    status["steps"]["ingest"] = "done"
    store.write_status(set_id, status, root=root)
    return set_id


# ---------------------------------------------------------------------------
# YouTube / yt-dlp-supported URL
# ---------------------------------------------------------------------------
def _fetch_url_metadata(url: str) -> dict[str, Any]:
    """``download=False`` probe — gives us title/uploader/duration up-front so we can
    compute ``set_id`` *before* writing any files."""
    from yt_dlp import YoutubeDL

    with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)  # JSON-safe


def _normalise_thumbnail(set_dir_path: Path) -> None:
    """yt-dlp may save the thumbnail as ``source.jpg`` (matching the source stem) or
    similar. Normalise whatever it dropped to ``thumbnail.jpg``."""
    target = set_dir_path / "thumbnail.jpg"
    if target.exists():
        return
    # Look for any ``source.{jpg,png,webp}`` or ``thumbnail.<ext>`` variants and rename.
    for ext in ("jpg", "jpeg", "png", "webp"):
        for stem in ("thumbnail", "source"):
            candidate = set_dir_path / f"{stem}.{ext}"
            if candidate.exists():
                candidate.rename(target)
                return


def ingest_url(url: str, *, root: Path | None = None) -> str:
    """Download a yt-dlp-supported URL into a new per-set dir. Returns set_id."""
    from yt_dlp import YoutubeDL

    info = _fetch_url_metadata(url)
    title = info.get("title") or info.get("id") or "untitled"
    source_id = info.get("webpage_url") or url
    set_id = store.make_set_id(title, source_id)
    d = store.set_dir(set_id, root=root)
    d.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict[str, Any] = {
        "format": URL_FORMAT,
        "outtmpl": {
            "default": str(d / "source.%(ext)s"),
            "thumbnail": str(d / "thumbnail.%(ext)s"),
        },
        "writethumbnail": True,
        "postprocessors": [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    _normalise_thumbnail(d)

    duration = info.get("duration")
    meta: dict[str, Any] = {
        "title": title,
        "source_url": source_id,
        "uploader": info.get("uploader") or info.get("channel"),
        "duration_s": float(duration) if duration is not None else None,
        "video_id": info.get("id"),
    }
    store.write_metadata(set_id, meta, root=root)
    status = store.init_status(set_id, root=root)
    status["steps"]["ingest"] = "done"
    store.write_status(set_id, status, root=root)
    return set_id


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def ingest(target: str | Path, *, root: Path | None = None) -> str:
    """Branch on URL vs local path. Returns the new set_id either way."""
    if isinstance(target, Path):
        return ingest_local(target, root=root)
    if _looks_like_url(target):
        return ingest_url(target, root=root)
    return ingest_local(Path(target), root=root)
