"""Integration test: BPM analysis on an m4a source goes through the cached
WAV without falling back to librosa's audioread path.

This is the perf-fix smoke that prevents regressions where future code
accidentally bypasses the cache for non-native formats."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from setplot.pipeline import bpm

from .conftest import FIXTURES


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_bpm_run_on_m4a_uses_decode_cache(tmp_path):
    # Build an m4a (AAC-in-MP4) from the bundled mp3 fixture.
    m4a = tmp_path / "clip30.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(FIXTURES / "clip30.mp3"),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-y",
            str(m4a),
        ],
        check=True,
        capture_output=True,
    )

    # Pin to librosa so we exercise the cache path that replaces the audioread
    # fallback. (essentia's path uses a separate 44100 cache and was the whole
    # reason librosa needed audioread in the first place.)
    csv_path = bpm.run(m4a, step=2.0, window=8.0, chunk_min=1.0, engine="librosa")

    # The decode cache lives next to the source.
    cache = tmp_path / ".work-22050.wav"
    assert cache.exists()
    assert cache.stat().st_size > 0

    # CSV is well-formed and produced sensible BPM estimates.
    rows = csv_path.read_text().splitlines()
    assert rows[0] == "time_s,timestamp,bpm"
    assert len(rows) >= 10  # 30s at step=2 → ~12 windows
    # Same fixture content as the mp3 path; clip is at 129.2 BPM.
    bpms = [float(line.split(",")[2]) for line in rows[1:]]
    assert all(120 < b < 140 for b in bpms), bpms
