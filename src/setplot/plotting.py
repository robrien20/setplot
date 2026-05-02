"""Overlay BPM-over-time with ACR tracklist identification coverage.

Produces a single figure with two stacked panels sharing the time axis:

  Panel 1 (BPM):
    - Genre-zone bands as background (house, techno, d&b, etc.)
    - Per-window BPM line (light) + rolling-mean BPM (dark)
    - Red shading for unknown regions ≥60s (no ACR match at all)
    - Labels at the start of the N longest-playing identified tracks
    - Vertical lines at estimated track starts for those top tracks

  Panel 2 (coverage strip):
    - One colored cell per 10s ACR window:
        dark green  = strong match, single candidate (confident, clean)
        light green = strong match, multiple candidates (layered or alternate versions)
        yellow      = medium confidence
        orange      = weak-only match
        gray        = no match at all
    - Reveals where the set has dense identification vs dark zones
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

GENRE_BANDS = [
    (60, 90, "hip-hop / downtempo", "#f0f4f8"),
    (90, 110, "house (slow) / dub", "#e4eef7"),
    (110, 128, "house / disco / tech-house", "#d0e0f0"),
    (128, 140, "techno / trance", "#b8d0e8"),
    (140, 160, "hard techno / hardgroove / psytrance", "#a0c0e0"),
    (160, 180, "drum & bass / jungle (half-time 80-90)", "#88b0d8"),
    (180, 220, "hardcore / gabber / speed", "#6890c8"),
]

# Coverage-strip colors
COLOR_NO_MATCH = "#aaaaaa"
COLOR_WEAK = "#ff9800"
COLOR_MEDIUM = "#ffd54f"
COLOR_STRONG_CLEAN = "#2e7d32"
COLOR_STRONG_LAYERED = "#66bb6a"


def load_bpm(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    t, v = [], []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            t.append(float(r["time_s"]))
            v.append(float(r["bpm"]))
    return np.array(t), np.array(v)


def build_window_info(raw_hits: list, stride: int, duration_s: float) -> dict:
    by_window: dict[int, list] = defaultdict(list)
    for h in raw_hits:
        by_window[int(h["_window_start_s"])].append(h)

    info = {}
    for w in range(0, int(duration_s) + stride, stride):
        hs = by_window.get(w, [])
        if not hs:
            info[w] = (0, 0, False, "", "")
        else:
            top = max(hs, key=lambda h: int(h.get("score", 0) or 0))
            score = int(top.get("score", 0) or 0)
            artists = ", ".join(a.get("name", "") for a in top.get("artists", []) or [])
            info[w] = (score, len(hs), True, artists, top.get("title", ""))
    return info


def find_runs(windows: list[int], stride: int, predicate, min_duration_s: float) -> list[tuple[int, int]]:
    spans = []
    cur_start = None
    for w in windows:
        if predicate(w):
            if cur_start is None:
                cur_start = w
        else:
            if cur_start is not None:
                end = w
                if end - cur_start >= min_duration_s:
                    spans.append((cur_start, end))
                cur_start = None
    if cur_start is not None:
        end = windows[-1] + stride
        if end - cur_start >= min_duration_s:
            spans.append((cur_start, end))
    return spans


def plot_overlay(
    bpm_csv: Path, acr_json: Path, out_path: Path, top_labels: int = 20, stride: int = 10
) -> None:
    bpm_t, bpm_v = load_bpm(bpm_csv)
    d = json.loads(Path(acr_json).read_text())
    merged = d["merged"]
    raw_hits = d["raw_hits"]
    duration_s = float(bpm_t[-1]) + 24
    window_info = build_window_info(raw_hits, stride, duration_s)
    windows = sorted(window_info.keys())

    no_match_runs = find_runs(windows, stride, lambda w: not window_info[w][2], min_duration_s=60)
    weak_only_runs = find_runs(
        windows,
        stride,
        lambda w: window_info[w][2] and window_info[w][0] < 40,
        min_duration_s=60,
    )
    layered_runs = find_runs(
        windows,
        stride,
        lambda w: window_info[w][2] and window_info[w][1] >= 2 and window_info[w][0] >= 50,
        min_duration_s=30,
    )

    top_tracks = sorted(merged, key=lambda e: -e["hit_count"])[:top_labels]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(26, 11), gridspec_kw={"height_ratios": [3, 0.7]}, sharex=True
    )

    # ========== Panel 1: BPM ==========
    for lo, hi, label, color in GENRE_BANDS:
        ax1.axhspan(lo, hi, color=color, alpha=0.55, zorder=0)
        ax1.text(
            duration_s / 3600 * 1.005,
            (lo + hi) / 2,
            label,
            fontsize=7,
            va="center",
            ha="left",
            color="#333",
            alpha=0.8,
        )

    for s, e in no_match_runs:
        ax1.axvspan(
            s / 3600,
            e / 3600,
            facecolor="#ff3333",
            alpha=0.14,
            zorder=0.3,
            hatch="///",
            edgecolor="#aa0000",
            linewidth=0,
        )
    for s, e in weak_only_runs:
        ax1.axvspan(s / 3600, e / 3600, color="#ff9800", alpha=0.10, zorder=0.4)
    for s, e in layered_runs:
        ax1.axvspan(s / 3600, e / 3600, ymin=0.0, ymax=0.035, color=COLOR_STRONG_LAYERED, alpha=0.9, zorder=1)

    k = 11
    if len(bpm_v) >= k:
        smoothed = np.convolve(bpm_v, np.ones(k) / k, mode="same")
    else:
        smoothed = bpm_v
    ax1.plot(bpm_t / 3600, bpm_v, linewidth=0.4, alpha=0.30, color="#1a4480", zorder=2)
    ax1.plot(
        bpm_t / 3600, smoothed, linewidth=1.4, color="#0a2a5a", zorder=3, label=f"{k}-window rolling mean"
    )

    top_sorted = sorted(top_tracks, key=lambda e: e["estimated_track_start_s"])
    min_spacing_h = duration_s / 3600 / 70
    n_rows = 6
    row_last_end_h = [-1e9] * n_rows
    y_rows = [215 - i * 5 for i in range(n_rows)]
    for t in top_sorted:
        ts_h = t["estimated_track_start_s"] / 3600
        label = f"{t['artists'][:22]} — {t['title'][:28]} ({t['hit_count']}x)"
        placed = False
        for row in range(n_rows):
            if ts_h >= row_last_end_h[row] + min_spacing_h:
                y = y_rows[row]
                ax1.axvline(
                    ts_h,
                    color="black",
                    linewidth=0.35,
                    alpha=0.35,
                    zorder=1.5,
                    ymin=0,
                    ymax=(y - 60) / (220 - 60),
                )
                ax1.annotate(
                    label,
                    xy=(ts_h, y),
                    xytext=(ts_h + min_spacing_h * 0.15, y),
                    fontsize=6,
                    ha="left",
                    va="center",
                    color="#1a1a1a",
                    alpha=0.95,
                    zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#666", lw=0.4, alpha=0.85),
                )
                row_last_end_h[row] = ts_h + min_spacing_h
                placed = True
                break
        if not placed:
            continue

    ax1.set_ylabel("BPM")
    ax1.set_ylim(60, 220)
    ax1.grid(True, axis="y", alpha=0.25)

    from matplotlib.patches import Patch

    legend_elems = [
        Patch(
            facecolor="#ff3333",
            alpha=0.14,
            hatch="///",
            edgecolor="#aa0000",
            label=f"unknown region ≥60s ({len(no_match_runs)})",
        ),
        Patch(facecolor="#ff9800", alpha=0.25, label=f"weak-only region ≥60s ({len(weak_only_runs)})"),
        Patch(facecolor=COLOR_STRONG_LAYERED, label=f"layered/multi-candidate ≥30s ({len(layered_runs)})"),
    ]
    ax1.legend(handles=legend_elems, loc="upper left", fontsize=8, framealpha=0.9)

    total_unknown_s = sum(e - s for s, e in no_match_runs)
    total_weak_s = sum(e - s for s, e in weak_only_runs)
    title = (
        f"BPM + ACR coverage overlay  |  "
        f"{len(merged)} unique tracks | "
        f"unknown ≥60s: {total_unknown_s // 60}min ({total_unknown_s / duration_s * 100:.0f}%) | "
        f"weak-only ≥60s: {total_weak_s // 60}min"
    )
    ax1.set_title(title)

    # ========== Panel 2: Coverage strip ==========
    strip_colors = []
    for w in windows:
        score, n_cand, has, _, _ = window_info[w]
        if not has:
            strip_colors.append(COLOR_NO_MATCH)
        elif score >= 80 and n_cand == 1:
            strip_colors.append(COLOR_STRONG_CLEAN)
        elif score >= 80:
            strip_colors.append(COLOR_STRONG_LAYERED)
        elif score >= 40:
            strip_colors.append(COLOR_MEDIUM)
        else:
            strip_colors.append(COLOR_WEAK)

    x_positions = np.array(windows) / 3600
    ax2.bar(
        x_positions,
        [1] * len(windows),
        width=stride / 3600,
        color=strip_colors,
        align="edge",
        edgecolor="none",
    )
    ax2.set_ylim(0, 1)
    ax2.set_yticks([])
    ax2.set_ylabel("coverage")
    ax2.set_xlabel("time (hours)")
    ax2.set_xlim(0, duration_s / 3600)

    legend_elems2 = [
        Patch(facecolor=COLOR_STRONG_CLEAN, label="strong + clean (score≥80, 1 cand.)"),
        Patch(facecolor=COLOR_STRONG_LAYERED, label="strong + layered (score≥80, 2+ cand.)"),
        Patch(facecolor=COLOR_MEDIUM, label="medium (40-79)"),
        Patch(facecolor=COLOR_WEAK, label="weak only (<40)"),
        Patch(facecolor=COLOR_NO_MATCH, label="no match"),
    ]
    ax2.legend(
        handles=legend_elems2,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.45),
        ncol=5,
        fontsize=8,
        framealpha=0.9,
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote: {out_path}")
    print(
        f"  total unknown ≥60s: {total_unknown_s}s = {total_unknown_s / 60:.1f}min "
        f"({total_unknown_s / duration_s * 100:.1f}% of set)"
    )
    print(f"  total weak-only ≥60s: {total_weak_s}s = {total_weak_s / 60:.1f}min")
    print(f"  layered strong regions ≥30s: {len(layered_runs)}")


def plot_hour_zoom(
    bpm_csv: Path, acr_json: Path, out_dir: Path, stride: int = 10, hour_span: float = 1.0
) -> None:
    """Split the timeline into hour_span-hour slices and render each as its own plot with
    all tracks labeled (not just top-N). Much more readable for close inspection."""
    bpm_t, bpm_v = load_bpm(bpm_csv)
    d = json.loads(Path(acr_json).read_text())
    merged = d["merged"]
    raw_hits = d["raw_hits"]
    duration_s = float(bpm_t[-1]) + 24
    window_info = build_window_info(raw_hits, stride, duration_s)

    n_slices = int(np.ceil(duration_s / 3600 / hour_span))
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n_slices):
        t_start_s = i * hour_span * 3600
        t_end_s = min((i + 1) * hour_span * 3600, duration_s)
        slice_png = out_dir / f"zoom_{i:02d}_{int(t_start_s / 60):04d}m-{int(t_end_s / 60):04d}m.png"

        bpm_mask = (bpm_t >= t_start_s) & (bpm_t < t_end_s)
        bpm_t_s = bpm_t[bpm_mask]
        bpm_v_s = bpm_v[bpm_mask]
        tracks_in_slice = [t for t in merged if t_start_s <= t["estimated_track_start_s"] < t_end_s]
        windows_in_slice = [w for w in sorted(window_info) if t_start_s <= w < t_end_s]
        no_match = find_runs(windows_in_slice, stride, lambda w: not window_info[w][2], min_duration_s=30)

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(22, 8), gridspec_kw={"height_ratios": [3, 0.8]}, sharex=True
        )
        for lo, hi, label, color in GENRE_BANDS:
            ax1.axhspan(lo, hi, color=color, alpha=0.55, zorder=0)
            ax1.text(
                t_end_s / 60 * 1.004,
                (lo + hi) / 2,
                label,
                fontsize=7,
                va="center",
                ha="left",
                color="#333",
                alpha=0.8,
            )
        for s, e in no_match:
            ax1.axvspan(
                s / 60,
                e / 60,
                facecolor="#ff3333",
                alpha=0.14,
                zorder=0.3,
                hatch="///",
                edgecolor="#aa0000",
                linewidth=0,
            )
        k = 7
        if len(bpm_v_s) >= k:
            smoothed = np.convolve(bpm_v_s, np.ones(k) / k, mode="same")
        else:
            smoothed = bpm_v_s
        ax1.plot(bpm_t_s / 60, bpm_v_s, linewidth=0.5, alpha=0.35, color="#1a4480", zorder=2)
        ax1.plot(bpm_t_s / 60, smoothed, linewidth=1.3, color="#0a2a5a", zorder=3)

        sorted_tracks = sorted(tracks_in_slice, key=lambda e: e["estimated_track_start_s"])
        n_rows = 6
        min_spacing_min = (t_end_s - t_start_s) / 60 / 40
        row_last = [-1e9] * n_rows
        y_rows = [216 - j * 5 for j in range(n_rows)]
        for t in sorted_tracks:
            ts_m = t["estimated_track_start_s"] / 60
            label = f"{t['artists'][:20]} — {t['title'][:26]} ({t['hit_count']}x,{t['best_score']})"
            for row in range(n_rows):
                if ts_m >= row_last[row] + min_spacing_min:
                    y = y_rows[row]
                    ax1.axvline(
                        ts_m,
                        color="black",
                        linewidth=0.3,
                        alpha=0.35,
                        zorder=1.5,
                        ymin=0,
                        ymax=(y - 60) / (220 - 60),
                    )
                    ax1.annotate(
                        label,
                        xy=(ts_m, y),
                        xytext=(ts_m + min_spacing_min * 0.1, y),
                        fontsize=6,
                        ha="left",
                        va="center",
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#666", lw=0.4, alpha=0.85),
                    )
                    row_last[row] = ts_m + min_spacing_min
                    break

        ax1.set_ylim(60, 220)
        ax1.set_xlim(t_start_s / 60, t_end_s / 60)
        ax1.set_ylabel("BPM")
        ax1.grid(True, axis="y", alpha=0.25)
        h1, m1 = int(t_start_s // 3600), int((t_start_s % 3600) // 60)
        h2, m2 = int(t_end_s // 3600), int((t_end_s % 3600) // 60)
        ax1.set_title(
            f"{h1:02d}:{m1:02d} to {h2:02d}:{m2:02d}  "
            f"({len(tracks_in_slice)} tracks, {len(no_match)} unknown gaps ≥30s)"
        )

        strip_colors = []
        for w in windows_in_slice:
            score, n_cand, has, _, _ = window_info[w]
            if not has:
                strip_colors.append(COLOR_NO_MATCH)
            elif score >= 80 and n_cand == 1:
                strip_colors.append(COLOR_STRONG_CLEAN)
            elif score >= 80:
                strip_colors.append(COLOR_STRONG_LAYERED)
            elif score >= 40:
                strip_colors.append(COLOR_MEDIUM)
            else:
                strip_colors.append(COLOR_WEAK)
        x_positions = np.array(windows_in_slice) / 60
        ax2.bar(
            x_positions,
            [1] * len(windows_in_slice),
            width=stride / 60,
            color=strip_colors,
            align="edge",
            edgecolor="none",
        )
        ax2.set_ylim(0, 1)
        ax2.set_yticks([])
        ax2.set_xlabel("time (minutes)")
        ax2.set_xlim(t_start_s / 60, t_end_s / 60)

        plt.tight_layout()
        plt.savefig(slice_png, dpi=120, bbox_inches="tight")
        plt.close(fig)
        print(f"  {slice_png.name}  ({len(tracks_in_slice)} tracks)")
