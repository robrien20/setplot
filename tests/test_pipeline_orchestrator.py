"""End-to-end orchestrator on a 30s fixture: ingest + bpm + key, with peaks
and fingerprint deliberately skipped (audiowaveform may not be installed,
ACR creds aren't set in CI)."""

from __future__ import annotations

import json
import shutil

import pytest

from setplot import store
from setplot.pipeline import ingest as ingest_mod
from setplot.pipeline import orchestrator

from .conftest import FIXTURES


def test_analyze_runs_bpm_and_key_writes_status(tmp_path):
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    skip: tuple[str, ...] = ("fingerprint",)
    if shutil.which("audiowaveform") is None:
        skip = (*skip, "peaks")

    final = orchestrator.analyze(
        sid,
        root=tmp_path,
        bpm_step=2.0,
        bpm_window=8.0,
        key_step=4.0,
        key_window=12.0,
        key_engine="librosa",
        chunk_min=1.0,
        skip=skip,
    )
    assert final["bpm"] == "done"
    assert final["key"] == "done"
    assert final["fingerprint"] == "skipped"

    bpm_path = store.step_output_path(sid, "bpm", root=tmp_path)
    key_path = store.step_output_path(sid, "key", root=tmp_path)
    assert bpm_path.exists()
    assert key_path.exists()
    bpm_doc = json.loads(bpm_path.read_text())
    assert bpm_doc["step_s"] == 2.0
    assert bpm_doc["window_s"] == 8.0
    assert isinstance(bpm_doc["data"], list) and len(bpm_doc["data"]) >= 10
    # Each entry is [t, bpm].
    assert all(len(row) == 2 for row in bpm_doc["data"])

    key_doc = json.loads(key_path.read_text())
    assert key_doc["engine"] == "librosa"
    assert key_doc["step_s"] == 4.0
    # Each entry is [t, camelot, name, corr, margin].
    assert all(len(row) == 5 for row in key_doc["data"])
    assert all(isinstance(row[1], str) for row in key_doc["data"])


def test_analyze_emits_events_via_callback(tmp_path):
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    seen: list[dict] = []

    skip = ("fingerprint", "peaks")
    orchestrator.analyze(
        sid,
        root=tmp_path,
        bpm_step=4.0,
        bpm_window=8.0,
        key_step=4.0,
        key_window=12.0,
        key_engine="librosa",
        chunk_min=1.0,
        skip=skip,
        event_cb=seen.append,
    )

    states = [(e["step"], e["state"]) for e in seen]
    assert ("peaks", "skipped") in states
    assert ("fingerprint", "skipped") in states
    assert ("bpm", "running") in states
    assert ("bpm", "done") in states
    assert ("key", "done") in states


def test_analyze_unknown_skip_raises(tmp_path):
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    with pytest.raises(ValueError):
        orchestrator.analyze(sid, root=tmp_path, skip=("wibble",))


def test_analyze_records_failure_on_step(tmp_path, monkeypatch):
    """When a step raises, status reflects 'failed: …' and subsequent steps still run."""
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)

    def boom(*_a, **_kw):
        raise RuntimeError("deliberate kaboom")

    monkeypatch.setattr(orchestrator, "_run_bpm", boom)
    final = orchestrator.analyze(
        sid,
        root=tmp_path,
        key_step=4.0,
        key_window=12.0,
        key_engine="librosa",
        chunk_min=1.0,
        skip=("peaks", "fingerprint"),
    )
    assert final["bpm"].startswith("failed:")
    assert final["key"] == "done"  # didn't tank
