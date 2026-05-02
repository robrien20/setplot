"""Parity test for the librosa key engine + skip-marker for the essentia engine."""

from __future__ import annotations

import shutil

import pytest

from setplot.pipeline import key

from .conftest import FIXTURES


def test_key_scale_to_camelot_maps_known_keys():
    pc, mode, cam = key.key_scale_to_camelot("F#", "major")
    assert (pc, mode, cam) == (6, "maj", "2B")
    pc, mode, cam = key.key_scale_to_camelot("A", "minor")
    assert (pc, mode, cam) == (9, "min", "8A")
    # enharmonic normalization
    pc, mode, cam = key.key_scale_to_camelot("Db", "major")
    assert pc == 1


def test_key_run_librosa_matches_legacy_output(tmp_path):
    src = FIXTURES / "clip30.mp3"
    expected = (FIXTURES / "clip30.expected_key_librosa.csv").read_text()
    work = tmp_path / "clip30.mp3"
    shutil.copy(src, work)

    csv_path = key.run(work, step=4.0, window=12.0, chunk_min=1.0, engine="librosa")
    assert csv_path.read_text() == expected


@pytest.mark.skipif(not key.HAS_ESSENTIA, reason="essentia not installed")
def test_key_run_essentia_runs_and_writes_csv(tmp_path):
    src = FIXTURES / "clip30.mp3"
    work = tmp_path / "clip30.mp3"
    shutil.copy(src, work)
    csv_path = key.run(work, step=10.0, window=12.0, chunk_min=1.0, engine="essentia")
    assert csv_path.exists()
    assert csv_path.read_text().startswith("time_s,timestamp,camelot,key,strength,margin")
