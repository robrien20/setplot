"""``audiowaveform``-dependent peaks generation. Skips cleanly when the binary
isn't installed locally."""

from __future__ import annotations

import json
import shutil

import pytest

from setplot.pipeline import ingest as ingest_mod
from setplot.pipeline import peaks as peaks_mod

from .conftest import FIXTURES


def test_audiowaveform_missing_raises_friendly_error(tmp_path, monkeypatch):
    monkeypatch.setattr(peaks_mod, "audiowaveform_available", lambda: False)
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    with pytest.raises(peaks_mod.AudioWaveformMissingError):
        peaks_mod.make_peaks(sid, root=tmp_path)


@pytest.mark.skipif(shutil.which("audiowaveform") is None, reason="audiowaveform not installed")
def test_make_peaks_writes_json(tmp_path):
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    out = peaks_mod.make_peaks(sid, root=tmp_path)
    assert out.exists()
    payload = json.loads(out.read_text())
    # bbc/audiowaveform's output JSON has these top-level keys.
    assert "data" in payload
    assert "sample_rate" in payload
    assert isinstance(payload["data"], list)
    assert len(payload["data"]) > 0
