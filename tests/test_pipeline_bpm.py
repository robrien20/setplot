"""Parity test: the ported `setplot.pipeline.bpm.run` produces output identical
to the legacy `bpm_over_time.py` script for the same inputs.

The legacy CSV was captured once into tests/fixtures/clip30.expected_bpm.csv
using the original script before the port. This test asserts byte-for-byte
equality of the generated CSV.
"""

from __future__ import annotations

import shutil

from setplot.pipeline import bpm

from .conftest import FIXTURES


def test_octave_fix_folds_into_band():
    assert bpm._octave_fix(60.0) == 120.0  # too slow → ×2
    assert bpm._octave_fix(220.0) == 110.0  # too fast → ÷2
    assert bpm._octave_fix(0.0) == 0.0
    assert bpm._octave_fix(130.0) == 130.0


def test_bpm_run_matches_legacy_output(tmp_path):
    """End-to-end parity against the captured legacy CSV (librosa engine).

    The expected CSV was captured from the legacy ``bpm_over_time.py`` script
    which used librosa autocorrelation. We pin engine='librosa' so this stays
    a regression check on the librosa code path even after essentia became
    the default.
    """
    src = FIXTURES / "clip30.mp3"
    expected = (FIXTURES / "clip30.expected_bpm.csv").read_text()
    work = tmp_path / "clip30.mp3"
    shutil.copy(src, work)

    csv_path = bpm.run(work, step=2.0, window=8.0, chunk_min=1.0, engine="librosa")

    assert csv_path.read_text() == expected


def test_bpm_run_essentia_produces_sensible_output(tmp_path):
    """Smoke the essentia engine. Fixture is a 30s clip at 129.2 BPM (constant)."""
    if not bpm.HAS_ESSENTIA:
        import pytest

        pytest.skip("essentia not installed")
    src = FIXTURES / "clip30.mp3"
    work = tmp_path / "clip30.mp3"
    shutil.copy(src, work)
    csv_path = bpm.run(work, step=4.0, window=12.0, chunk_min=1.0, engine="essentia")
    rows = [line.split(",") for line in csv_path.read_text().splitlines()[1:]]
    assert len(rows) >= 4
    bpms = [float(r[2]) for r in rows]
    # Essentia tends to lock to either the half-time or full-time multiple; both are
    # within DJ-set-plausible range for this 129.2 BPM fixture.
    assert all(60 <= b <= 200 for b in bpms), bpms
