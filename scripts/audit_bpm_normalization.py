"""Audit BPM normalization across every ingested set.

For each bpm.json we re-run the same fold heuristic the orchestrator uses, but
across a *grid* of (window, tol) settings, so we can see where points slip
through. We also report on cluster behavior — a cluster of half-BPM points
won't be folded by the local-median test (because they'll dominate the median
in their own neighborhood), and it's clusters that wreck the chart's
percentile-based y-autoscale.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from setplot.config import get_settings


def fold_octave_outliers(
    pairs: list[tuple[float, float]], window_pts: int, tol: float
) -> tuple[list[tuple[float, float]], list[tuple[int, float, float, float]]]:
    """Reproduces fold_octave_outliers but also returns the *folded* records
    as (idx, t, original_bpm, median_used)."""
    if len(pairs) < 5:
        return list(pairs), []
    bpms = [b for _, b in pairs]
    half_w = window_pts // 2
    out: list[tuple[float, float]] = []
    folded: list[tuple[int, float, float, float]] = []
    for i, (t, bpm) in enumerate(pairs):
        lo = max(0, i - half_w)
        hi = min(len(bpms), i + half_w + 1)
        neighbours = bpms[lo:i] + bpms[i + 1 : hi]
        if len(neighbours) < 5:
            out.append((t, bpm))
            continue
        m = sorted(neighbours)[len(neighbours) // 2]
        if m <= 0:
            out.append((t, bpm))
            continue
        ratio = bpm / m
        if abs(ratio - 0.5) < tol:
            out.append((t, bpm * 2))
            folded.append((i, t, bpm, m))
        elif abs(ratio - 2.0) < tol:
            out.append((t, bpm / 2))
            folded.append((i, t, bpm, m))
        else:
            out.append((t, bpm))
    return out, folded


def detect_global_half_clusters(
    pairs: list[tuple[float, float]], min_run: int = 3
) -> list[tuple[float, float, float, float]]:
    """Find runs of points whose BPM is roughly half (or double) the *global*
    track median. Local-median fold won't catch these because the cluster
    dominates its own neighborhood.

    Returns list of (start_t, end_t, run_median_bpm, global_median_bpm).
    """
    if len(pairs) < 20:
        return []
    bpms = [b for _, b in pairs]
    g = median(bpms)
    if g <= 0:
        return []
    runs: list[tuple[float, float, float, float]] = []
    cur: list[tuple[float, float]] = []
    for t, b in pairs:
        if b <= 0:
            if len(cur) >= min_run:
                run_med = median([x[1] for x in cur])
                runs.append((cur[0][0], cur[-1][0], run_med, g))
            cur = []
            continue
        ratio = b / g
        if abs(ratio - 0.5) < 0.08 or abs(ratio - 2.0) < 0.08:
            cur.append((t, b))
        else:
            if len(cur) >= min_run:
                run_med = median([x[1] for x in cur])
                runs.append((cur[0][0], cur[-1][0], run_med, g))
            cur = []
    if len(cur) >= min_run:
        run_med = median([x[1] for x in cur])
        runs.append((cur[0][0], cur[-1][0], run_med, g))
    return runs


def fmt(t: float) -> str:
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def audit_one(path: Path) -> None:
    payload = json.loads(path.read_text())
    data: list[tuple[float, float]] = [(float(t), float(b)) for t, b in payload.get("data", [])]
    if not data:
        print(f"\n== {path.parent.name} ==  (empty)")
        return
    bpms = [b for _, b in data]
    g = median(bpms)
    p_lo = sorted(bpms)[max(0, int(len(bpms) * 0.02))]
    p_hi = sorted(bpms)[min(len(bpms) - 1, int(len(bpms) * 0.98))]
    print(f"\n== {path.parent.name} ==")
    print(
        f"  n={len(data)}  step={payload.get('step_s')}s  engine={payload.get('engine')}  "
        f"min={min(bpms):.1f}  median={g:.1f}  max={max(bpms):.1f}  p2={p_lo:.1f}  p98={p_hi:.1f}"
    )

    # Re-apply the production fold (window=21, tol=0.06) to see if anything
    # would *still* be folded — i.e. signs that the saved data was missed.
    _, prod_folded = fold_octave_outliers(data, window_pts=21, tol=0.06)
    print(f"  prod-fold (window=21, tol=0.06): would still fold {len(prod_folded)} points")

    # Wider tolerance: catches near-misses (e.g. essentia returning 71 vs median 142
    # — ratio 0.50 ± 0.07)
    _, wide_folded = fold_octave_outliers(data, window_pts=31, tol=0.12)
    extra = len(wide_folded) - len(prod_folded)
    print(f"  wider (window=31, tol=0.12):    would fold {len(wide_folded)} points (+{extra} vs prod)")
    if extra:
        seen = {i for i, *_ in prod_folded}
        for i, t, bpm, m in wide_folded:
            if i in seen:
                continue
            ratio = bpm / m
            print(f"    near-miss: t={fmt(t)}  bpm={bpm:6.2f}  local_med={m:6.2f}  ratio={ratio:.3f}")

    # Cluster detection — points half/double the *global* median that group together.
    clusters = detect_global_half_clusters(data, min_run=3)
    if clusters:
        print(f"  global-median half/double CLUSTERS (≥3 pts): {len(clusters)}")
        for s, e, run_med, gm in clusters[:8]:
            print(
                f"    {fmt(s)}–{fmt(e)}  ({(e - s):.0f}s)  "
                f"run_median={run_med:.1f}  global_median={gm:.1f}  ratio={run_med / gm:.2f}"
            )
        if len(clusters) > 8:
            print(f"    ... +{len(clusters) - 8} more")

    # Y-axis impact: with the viewer's p2/p98 autoscale, do half-BPM clusters
    # actually leak into the visible range? p2 < global_median/1.7 means the
    # bottom of the chart is being pulled into "half-tempo land".
    if p_lo < g / 1.7:
        print(
            f"  ⚠️  chart-scale impact: p2={p_lo:.1f} is far below median {g:.1f} — "
            f"y-axis will compress real data"
        )


def main() -> None:
    root = get_settings().data_dir()
    print(f"data dir: {root}")
    sets = sorted(p for p in root.iterdir() if p.is_dir())
    bpms = [(s, s / "bpm.json") for s in sets if (s / "bpm.json").exists()]
    print(f"found {len(bpms)} sets with bpm.json")
    for _, p in bpms:
        audit_one(p)


if __name__ == "__main__":
    main()
