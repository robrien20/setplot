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


def test_global_tempo_anchor_pure_tempo():
    """A flat 144 BPM track returns ~144 as the anchor."""
    pairs = [(t * 5.0, 144.0) for t in range(100)]
    anchor = bpm.global_tempo_anchor(pairs)
    assert 143 <= anchor <= 145


def test_global_tempo_anchor_robust_to_half_tempo_cluster():
    """A small (10%) half-tempo cluster does not pull the anchor."""
    pairs = [(t * 5.0, 144.0 if t < 90 else 72.0) for t in range(100)]
    anchor = bpm.global_tempo_anchor(pairs)
    assert 143 <= anchor <= 145, f"anchor pulled to {anchor} by minority cluster"


def test_global_tempo_anchor_robust_to_large_cluster():
    """Even a 35% half-tempo cluster — too big for local-median fold to touch —
    must not become the anchor. This is the case the median-anchor approach
    handles only marginally; mode/binning handles it cleanly."""
    pairs = [(t * 5.0, 144.0 if t < 65 else 72.0) for t in range(100)]
    anchor = bpm.global_tempo_anchor(pairs)
    assert 143 <= anchor <= 145, f"anchor pulled to {anchor} by 35% cluster"


def test_fold_against_anchor_folds_half_tempo_cluster():
    """Sustained half-tempo cluster (longer than local-median window) folds."""
    # 100s cluster at ~72 in an otherwise 144 BPM set
    pairs = [(t * 5.0, 72.0 if 50 <= t < 70 else 144.0) for t in range(100)]
    folded, n = bpm.fold_against_anchor(pairs, anchor=144.0, tol=0.08)
    assert n == 20
    # All formerly-72 points are now ~144
    cluster = [b for t, b in folded if 250 <= t <= 345]
    assert all(b > 140 for b in cluster), cluster


def test_fold_against_anchor_preserves_legitimate_slow_section():
    """A 90 BPM downtempo section in a 144-BPM set is real, not octave-error.
    Ratio 90/144 = 0.625 which is well outside ±0.08 of 0.5."""
    pairs = [(t * 5.0, 90.0 if t < 20 else 144.0) for t in range(100)]
    folded, n = bpm.fold_against_anchor(pairs, anchor=144.0, tol=0.08)
    assert n == 0
    assert folded[0][1] == 90.0


def test_fold_against_anchor_at_anchor_tempo_is_noop():
    """Points at the anchor tempo are not folded."""
    pairs = [(t * 5.0, 144.0) for t in range(100)]
    folded, n = bpm.fold_against_anchor(pairs, anchor=144.0, tol=0.08)
    assert n == 0
    assert folded == pairs


def test_fold_octave_outliers_with_global_pass_handles_long_cluster():
    """End-to-end: a 75-second half-tempo cluster (the ra-live pattern) gets folded
    even though the local-median window can't escape it."""
    # 75s cluster: 15 contiguous half-tempo points in a 100-point series
    pairs = [(t * 5.0, 72.0 if 30 <= t < 45 else 144.0) for t in range(100)]
    # Local-median fold alone fails on this — confirm baseline first
    _, n_local_only = bpm.fold_octave_outliers(pairs, window_pts=21, tol=0.06)
    assert n_local_only < 15, "local fold catches some edge points but not the cluster core"
    # With the global anchor pass added, the whole cluster folds
    folded, n_total = bpm.fold_with_anchor_then_local(pairs)
    assert n_total >= 15
    cluster = [b for t, b in folded if 150 <= t <= 220]
    assert all(b > 140 for b in cluster), cluster


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
