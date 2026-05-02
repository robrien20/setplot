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
    """End-to-end parity against the captured legacy CSV."""
    src = FIXTURES / "clip30.mp3"
    expected = (FIXTURES / "clip30.expected_bpm.csv").read_text()
    work = tmp_path / "clip30.mp3"
    shutil.copy(src, work)

    csv_path = bpm.run(work, step=2.0, window=8.0, chunk_min=1.0)

    assert csv_path.read_text() == expected
