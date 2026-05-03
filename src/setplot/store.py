"""On-disk layout for SetPlot's per-set data directory.

Each set lives at ``{data_dir}/{set_id}/`` with a fixed file layout:

    source.{ext}      original media (mp4 / m4a / mp3 …)
    thumbnail.jpg     fetched by yt-dlp when ingesting a URL
    metadata.json     {title, source_url, uploader, duration_s, ingested_at, set_id}
    peaks.json        bbc/audiowaveform output (Phase 2)
    bpm.json          per-step analyzer outputs (Phase 2)
    key.json
    tracks.json
    status.json       {analysis_version, steps: {ingest,peaks,bpm,key,fingerprint: state}}

The id is ``{slug}-{8charhash}`` so it's both human-readable and stable across
re-ingests of the same source.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import shutil
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from setplot.config import get_settings

ANALYSIS_VERSION = 1
STEPS = ("ingest", "peaks", "bpm", "key", "fingerprint")
StepState = str  # "pending" | "running" | "done" | "skipped" | "failed: <msg>"


def data_dir() -> Path:
    """Resolved on-disk data root. Created lazily by callers that write into it."""
    return get_settings().data_dir()


# ---------------------------------------------------------------------------
# set_id
# ---------------------------------------------------------------------------
_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def slugify(s: str, max_len: int = 40) -> str:
    """Lossy, ASCII-only slug suitable for a directory name."""
    # NFKD decomposes accented chars into base + combining mark; ASCII-encoding
    # then drops the combining marks, leaving the bare letter.
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = _SLUG_BAD.sub("-", s.strip().lower()).strip("-")
    if not s:
        return "untitled"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def short_hash(source: str) -> str:
    """8-char hex digest. Stable across runs for the same source string."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]


def make_set_id(title: str, source: str) -> str:
    """``{slug}-{8charhash}`` — slug from title, hash from a stable source identifier
    (URL for downloads, absolute path for local files)."""
    return f"{slugify(title)}-{short_hash(source)}"


# ---------------------------------------------------------------------------
# Per-set paths
# ---------------------------------------------------------------------------
def set_dir(set_id: str, *, root: Path | None = None) -> Path:
    return (root or data_dir()) / set_id


def metadata_path(set_id: str, *, root: Path | None = None) -> Path:
    return set_dir(set_id, root=root) / "metadata.json"


def status_path(set_id: str, *, root: Path | None = None) -> Path:
    return set_dir(set_id, root=root) / "status.json"


def step_output_path(set_id: str, step: str, *, root: Path | None = None) -> Path:
    """Resolves the canonical output filename for a step."""
    if step not in STEPS:
        raise ValueError(f"unknown step: {step!r}")
    name = {"bpm": "bpm.json", "key": "key.json", "fingerprint": "tracks.json", "peaks": "peaks.json"}.get(
        step
    )
    if name is None:
        raise ValueError(f"step {step!r} has no output file")
    return set_dir(set_id, root=root) / name


def find_source(set_id: str, *, root: Path | None = None) -> Path | None:
    """Return the source media file inside a set dir (whatever extension it has)."""
    d = set_dir(set_id, root=root)
    for child in sorted(d.glob("source.*")):
        if child.suffix.lower() in {".mp4", ".m4a", ".mp3", ".ogg", ".flac", ".wav", ".webm", ".opus"}:
            return child
    return None


# ---------------------------------------------------------------------------
# metadata.json + status.json read/write
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def write_metadata(set_id: str, meta: dict[str, Any], *, root: Path | None = None) -> Path:
    path = metadata_path(set_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**meta, "set_id": set_id}
    payload.setdefault("ingested_at", now_iso())
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def read_metadata(set_id: str, *, root: Path | None = None) -> dict[str, Any]:
    return json.loads(metadata_path(set_id, root=root).read_text())


def init_status(set_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Create a fresh status.json with all steps pending."""
    payload = {
        "analysis_version": ANALYSIS_VERSION,
        "steps": dict.fromkeys(STEPS, "pending"),
    }
    write_status(set_id, payload, root=root)
    return payload


def read_status(set_id: str, *, root: Path | None = None) -> dict[str, Any]:
    return json.loads(status_path(set_id, root=root).read_text())


def write_status(set_id: str, status: dict[str, Any], *, root: Path | None = None) -> Path:
    path = status_path(set_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, ensure_ascii=False))
    return path


def update_step(set_id: str, step: str, state: StepState, *, root: Path | None = None) -> dict[str, Any]:
    """Mutate ``status.json``: set ``steps[step] = state`` and persist."""
    if step not in STEPS:
        raise ValueError(f"unknown step: {step!r}")
    p = status_path(set_id, root=root)
    if p.exists():
        payload = read_status(set_id, root=root)
    else:
        payload = init_status(set_id, root=root)
    payload["steps"][step] = state
    write_status(set_id, payload, root=root)
    return payload


# ---------------------------------------------------------------------------
# Library scan + delete
# ---------------------------------------------------------------------------
def list_sets(*, root: Path | None = None) -> list[dict[str, Any]]:
    """Scan the data dir, return one dict per set with metadata + status."""
    r = root or data_dir()
    if not r.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(r.iterdir()):
        if not child.is_dir():
            continue
        meta_p = child / "metadata.json"
        status_p = child / "status.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        status = (
            json.loads(status_p.read_text())
            if status_p.exists()
            else {"analysis_version": ANALYSIS_VERSION, "steps": dict.fromkeys(STEPS, "pending")}
        )
        out.append({"set_id": child.name, "path": child, "metadata": meta, "status": status})
    return out


def delete_set(set_id: str, *, root: Path | None = None) -> bool:
    """Delete the per-set directory. Returns True if anything was removed."""
    d = set_dir(set_id, root=root)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def all_step_outputs(set_id: str, *, root: Path | None = None) -> Iterable[Path]:
    for s in ("bpm", "key", "fingerprint", "peaks"):
        yield step_output_path(set_id, s, root=root)
