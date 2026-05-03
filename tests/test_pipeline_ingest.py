"""Local-file ingest. URL ingest is integration-tested manually — we don't hit
YouTube from CI."""

from __future__ import annotations

import shutil

from setplot import store
from setplot.pipeline import ingest as ingest_mod

from .conftest import FIXTURES


def test_ingest_local_creates_set_dir(tmp_path):
    src = FIXTURES / "clip30.mp3"
    sid = ingest_mod.ingest_local(src, root=tmp_path)
    d = store.set_dir(sid, root=tmp_path)
    assert d.exists()
    assert (d / "source.mp3").exists()
    meta = store.read_metadata(sid, root=tmp_path)
    assert meta["set_id"] == sid
    assert meta["title"] == "clip30"
    assert meta["source_url"].startswith("file://")
    # ffprobe is available in our test env so duration should be populated.
    if meta["duration_s"] is not None:
        assert 25.0 <= meta["duration_s"] <= 35.0
    status = store.read_status(sid, root=tmp_path)
    assert status["steps"]["ingest"] == "done"
    assert status["steps"]["bpm"] == "pending"


def test_ingest_local_is_idempotent(tmp_path):
    src = FIXTURES / "clip30.mp3"
    sid1 = ingest_mod.ingest_local(src, root=tmp_path)
    sid2 = ingest_mod.ingest_local(src, root=tmp_path)
    assert sid1 == sid2


def test_ingest_dispatcher_routes_local_path(tmp_path):
    src = FIXTURES / "clip30.mp3"
    sid_a = ingest_mod.ingest(str(src), root=tmp_path)
    sid_b = ingest_mod.ingest_local(src, root=tmp_path)
    assert sid_a == sid_b


def test_ingest_local_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        ingest_mod.ingest_local(tmp_path / "nope.mp3", root=tmp_path)


def test_ingest_copies_into_correct_extension(tmp_path):
    """Copying preserves the source extension as ``source.<ext>``."""
    fake = tmp_path / "stuff.M4A"  # uppercase ext on disk
    shutil.copy(FIXTURES / "clip30.mp3", fake)
    sid = ingest_mod.ingest_local(fake, root=tmp_path)
    d = store.set_dir(sid, root=tmp_path)
    # `_ingest_local` lowercases the ext for consistency
    assert (d / "source.m4a").exists()
