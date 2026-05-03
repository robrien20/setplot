"""On-disk store: id generation, per-set layout, list/delete, status transitions."""

from __future__ import annotations

import json

import pytest

from setplot import store


def test_slugify_handles_unicode_and_punctuation():
    assert store.slugify("Yuma — Coachella 2026!!") == "yuma-coachella-2026"
    assert store.slugify("  multiple   spaces  ") == "multiple-spaces"
    assert store.slugify("") == "untitled"
    assert store.slugify("Café Niño") == "cafe-nino"


def test_short_hash_is_stable():
    assert store.short_hash("https://example.com/x") == store.short_hash("https://example.com/x")
    assert store.short_hash("a") != store.short_hash("b")
    assert len(store.short_hash("anything")) == 8


def test_make_set_id_combines_slug_and_hash():
    sid = store.make_set_id("Yuma Day 1", "https://www.youtube.com/watch?v=abc123")
    assert sid.startswith("yuma-day-1-")
    assert sid.split("-")[-1] == store.short_hash("https://www.youtube.com/watch?v=abc123")


def test_set_id_max_slug_length():
    long = "a very long title that goes on and on and should be truncated cleanly"
    sid = store.make_set_id(long, "src")
    # slug capped at 40 chars + dash + 8-char hash
    assert len(sid) <= 40 + 1 + 8


def test_init_and_update_status(tmp_path):
    sid = "demo-12345678"
    payload = store.init_status(sid, root=tmp_path)
    assert payload["analysis_version"] == store.ANALYSIS_VERSION
    assert all(payload["steps"][k] == "pending" for k in store.STEPS)

    after = store.update_step(sid, "bpm", "running", root=tmp_path)
    assert after["steps"]["bpm"] == "running"
    after2 = store.update_step(sid, "bpm", "done", root=tmp_path)
    assert after2["steps"]["bpm"] == "done"
    assert after2["steps"]["key"] == "pending"


def test_update_step_rejects_unknown_step(tmp_path):
    with pytest.raises(ValueError):
        store.update_step("x-deadbeef", "wibble", "done", root=tmp_path)


def test_write_metadata_round_trip(tmp_path):
    sid = "demo-12345678"
    store.write_metadata(
        sid,
        {"title": "Demo", "source_url": "file:///a.mp3", "duration_s": 30.0},
        root=tmp_path,
    )
    meta = store.read_metadata(sid, root=tmp_path)
    assert meta["set_id"] == sid
    assert meta["title"] == "Demo"
    assert "ingested_at" in meta


def test_list_sets_skips_dirs_without_metadata(tmp_path):
    # well-formed set
    sid_a = "alpha-aaaaaaaa"
    store.write_metadata(sid_a, {"title": "Alpha"}, root=tmp_path)
    store.init_status(sid_a, root=tmp_path)
    # bare directory (no metadata.json) — should be skipped
    (tmp_path / "orphan").mkdir()
    # corrupt metadata — also skipped
    bad_dir = tmp_path / "broken-12345678"
    bad_dir.mkdir()
    (bad_dir / "metadata.json").write_text("{not json")

    rows = store.list_sets(root=tmp_path)
    assert [r["set_id"] for r in rows] == [sid_a]
    assert rows[0]["metadata"]["title"] == "Alpha"


def test_delete_set(tmp_path):
    sid = "alpha-aaaaaaaa"
    store.write_metadata(sid, {"title": "x"}, root=tmp_path)
    assert store.delete_set(sid, root=tmp_path) is True
    assert store.delete_set(sid, root=tmp_path) is False  # already gone


def test_step_output_paths(tmp_path):
    sid = "demo-12345678"
    assert store.step_output_path(sid, "bpm", root=tmp_path).name == "bpm.json"
    assert store.step_output_path(sid, "key", root=tmp_path).name == "key.json"
    assert store.step_output_path(sid, "fingerprint", root=tmp_path).name == "tracks.json"
    assert store.step_output_path(sid, "peaks", root=tmp_path).name == "peaks.json"
    with pytest.raises(ValueError):
        store.step_output_path(sid, "ingest", root=tmp_path)


def test_find_source_locates_media_file(tmp_path):
    sid = "demo-12345678"
    d = store.set_dir(sid, root=tmp_path)
    d.mkdir(parents=True)
    (d / "source.m4a").write_bytes(b"\x00")
    (d / "thumbnail.jpg").write_bytes(b"\x00")
    found = store.find_source(sid, root=tmp_path)
    assert found is not None and found.name == "source.m4a"


def test_data_dir_respects_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path / "x"))
    assert store.data_dir() == tmp_path / "x"


def test_data_dir_defaults_to_platform_path(monkeypatch):
    monkeypatch.delenv("SETPLOT_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    p = store.data_dir()
    # darwin: ~/Library/Application Support/SetPlot/data
    # linux:  ~/.local/share/setplot/data
    assert "SetPlot/data" in str(p) or "setplot/data" in str(p)


def test_write_metadata_preserves_existing_ingested_at(tmp_path):
    sid = "demo-deadbeef"
    fixed = "2025-12-31T00:00:00+00:00"
    store.write_metadata(sid, {"title": "x", "ingested_at": fixed}, root=tmp_path)
    meta = json.loads((tmp_path / sid / "metadata.json").read_text())
    assert meta["ingested_at"] == fixed
