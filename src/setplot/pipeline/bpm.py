"""Map BPM over time for a long audio file (e.g., a multi-hour DJ set).

Approach:
- Stream the audio in chunks (avoids loading 7h+ into memory at once).
- For each chunk, compute time-varying tempo via librosa.feature.tempo(aggregate=None).
- Downsample to one BPM estimate per `step` seconds.
- Write CSV (time_s, bpm) and a PNG plot with genre/BPM-band shading.
"""

from __future__ import annotations

import csv
import warnings
from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np

try:
    import essentia
    import essentia.standard as es

    HAS_ESSENTIA = True
    essentia.log.warningActive = False
    essentia.log.infoActive = False
except ImportError:
    HAS_ESSENTIA = False

# librosa 0.10's audioread fallback path emits noisy FutureWarning + UserWarning
# every chunk for inputs libsndfile can't read natively (mp4/m4a — what yt-dlp
# delivers). The fallback works correctly, just chatters. Filter narrowly.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"librosa\..*")
warnings.filterwarnings("ignore", category=UserWarning, module=r"librosa\..*")

# Genre zones commonly used in DJ culture for reference bands on the plot.
GENRE_BANDS = [
    (60, 90, "hip-hop / downtempo", "#f0f4f8"),
    (90, 110, "house (slow) / dub", "#e4eef7"),
    (110, 128, "house / disco / tech-house", "#d0e0f0"),
    (128, 140, "techno / trance", "#b8d0e8"),
    (140, 160, "hard techno / hardgroove / psytrance", "#a0c0e0"),
    (160, 180, "drum & bass / jungle (half-time 80-90)", "#88b0d8"),
    (180, 220, "hardcore / gabber / speed", "#6890c8"),
]


def _octave_fix(bpm: float, lo: float = 90.0, hi: float = 200.0) -> float:
    """Fold BPM into a plausible DJ-set range by doubling/halving.

    librosa's autocorrelation-based tempo estimator frequently hits octave errors
    (returning 1/2 or 2x the real tempo). This heuristic folds into [lo, hi] — good
    enough for a set where we know we're in the 100-180 BPM neighborhood.
    """
    if bpm <= 0:
        return 0.0
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    return bpm


def sliding_tempo_for_chunk(
    y: np.ndarray, sr: int, step_s: float, window_s: float, start_bpm: float
) -> list[tuple[float, float]]:
    """Walk a chunk in step_s-spaced windows of length window_s, return (t, bpm) pairs.

    Using a manual sliding window (rather than librosa.feature.tempo over the whole chunk)
    keeps memory bounded and gives us exactly the time resolution we asked for.
    """
    out: list[tuple[float, float]] = []
    window_samples = int(window_s * sr)
    step_samples = int(step_s * sr)
    for start in range(0, len(y) - window_samples + 1, step_samples):
        clip = y[start : start + window_samples]
        # std_bpm widens the prior to handle genre swings across a DJ set.
        # start_bpm biases away from pathological octave errors.
        bpm_raw = librosa.feature.tempo(y=clip, sr=sr, start_bpm=start_bpm, std_bpm=8)
        bpm = _octave_fix(float(bpm_raw[0]))
        out.append((start / sr, bpm))
    return out


def scan_essentia(
    path: Path, step_s: float, window_s: float, chunk_min: float, sr: int = 44100
) -> list[tuple[float, float]]:
    """Walk the file in chunks, return (t, bpm) pairs via Essentia RhythmExtractor2013.

    RhythmExtractor2013(method='multifeature') combines onset detection + comb
    filter analysis — significantly more accurate on EDM than librosa's
    autocorrelation tempo. The estimator is octave-aware internally, so the
    ``_octave_fix`` heuristic the librosa path needs is irrelevant here.
    """
    if not HAS_ESSENTIA:
        raise RuntimeError("essentia is not installed; install setplot[essentia] or use engine='librosa'")

    from setplot.pipeline._decode import ensure_decoded_wav

    # Essentia's RhythmExtractor expects 44.1 kHz mono. Cache once.
    path = ensure_decoded_wav(path, sr=sr)
    duration = librosa.get_duration(path=str(path))
    chunk_s = chunk_min * 60
    overlap = window_s
    rhythm = es.RhythmExtractor2013(method="multifeature")
    results: list[tuple[float, float]] = []
    t = 0.0
    while t < duration:
        this_len = min(chunk_s + overlap, duration - t)
        # librosa.load via libsndfile (fast on the cached WAV); essentia wants float32.
        y, _ = librosa.load(str(path), sr=sr, mono=True, offset=t, duration=this_len)
        y = y.astype("float32", copy=False)
        window_samples = int(window_s * sr)
        step_samples = int(step_s * sr)
        for start in range(0, len(y) - window_samples + 1, step_samples):
            clip = y[start : start + window_samples]
            bpm_val, _ticks, _conf, _est, _intervals = rhythm(clip)
            abs_t = t + start / sr
            if results and abs_t <= results[-1][0] + step_s / 2:
                continue
            results.append((abs_t, float(bpm_val)))
        print(f"  essentia chunk t={t / 60:6.1f}min  total {len(results)}")
        t += chunk_s
    return results


def scan_file(
    path: Path, step_s: float, window_s: float, chunk_min: float, sr: int, start_bpm: float
) -> list[tuple[float, float]]:
    """Stream the file in chunk_min-minute chunks, collect BPM estimates per step."""
    from setplot.pipeline._decode import ensure_decoded_wav

    # Pre-decode mp4/m4a once (no-op for mp3/wav/flac); chunked librosa.load
    # then reads through libsndfile directly instead of the slow audioread path.
    path = ensure_decoded_wav(path, sr)
    duration = librosa.get_duration(path=str(path))
    chunk_s = chunk_min * 60
    # Overlap each chunk by window_s so a window that straddles a chunk boundary still fits.
    overlap = window_s
    results: list[tuple[float, float]] = []
    t = 0.0
    while t < duration:
        this_len = min(chunk_s + overlap, duration - t)
        y, _ = librosa.load(str(path), sr=sr, mono=True, offset=t, duration=this_len)
        chunk_pairs = sliding_tempo_for_chunk(y, sr, step_s, window_s, start_bpm)
        # De-dup: if we already emitted a (t, bpm) for a timestamp in this chunk's overlap
        # region with the prior chunk, skip it.
        for rel_t, bpm in chunk_pairs:
            abs_t = t + rel_t
            if results and abs_t <= results[-1][0] + step_s / 2:
                continue
            results.append((abs_t, bpm))
        print(f"  chunk t={t / 60:6.1f}min  emitted {len(chunk_pairs)}  total {len(results)}")
        t += chunk_s  # advance by chunk_s (NOT chunk_s+overlap) so overlap is re-read
    return results


def fmt_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_csv(pairs: list[tuple[float, float]], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "timestamp", "bpm"])
        for t, bpm in pairs:
            w.writerow([f"{t:.2f}", fmt_ts(t), f"{bpm:.2f}"])


def plot(pairs: list[tuple[float, float]], path: Path, title: str) -> None:
    times = np.array([p[0] for p in pairs]) / 3600  # hours
    bpms = np.array([p[1] for p in pairs])
    # Rolling median smooth for a cleaner visual (keeps step transitions, kills noise)
    window = 11
    if len(bpms) >= window:
        kernel = np.ones(window) / window
        smoothed = np.convolve(bpms, kernel, mode="same")
    else:
        smoothed = bpms

    fig, ax = plt.subplots(figsize=(20, 6))
    # Genre bands
    for lo, hi, label, color in GENRE_BANDS:
        ax.axhspan(lo, hi, color=color, alpha=0.6, zorder=0)
        ax.text(
            times[-1] * 1.005,
            (lo + hi) / 2,
            label,
            fontsize=7,
            va="center",
            ha="left",
            color="#333",
            alpha=0.8,
        )
    # Raw BPM (light) + smoothed (dark)
    ax.plot(times, bpms, linewidth=0.5, alpha=0.35, color="#1a4480", label="per-window BPM")
    ax.plot(times, smoothed, linewidth=1.5, color="#0a2a5a", label=f"{window}-window rolling mean")
    ax.set_xlabel("time (hours)")
    ax.set_ylabel("BPM")
    ax.set_title(title)
    ax.set_ylim(60, 220)
    ax.set_xlim(0, times[-1])
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote plot: {path}")


def run(
    file: Path,
    *,
    step: float = 5.0,
    window: float = 24.0,
    chunk_min: float = 10.0,
    sr: int = 22050,
    start_bpm: float = 130.0,
    engine: str = "essentia",
) -> Path:
    """Run BPM analysis end-to-end. Returns path to the CSV.

    ``engine="essentia"`` uses RhythmExtractor2013 (multi-feature, octave-aware,
    44.1 kHz). Falls back to librosa autocorrelation if essentia isn't installed.
    """
    file = Path(file)
    if not file.exists():
        raise FileNotFoundError(file)

    if engine == "essentia" and not HAS_ESSENTIA:
        print("essentia not installed — falling back to librosa")
        engine = "librosa"

    print(f"file: {file}  engine={engine}  step={step}s  window={window}s  chunk={chunk_min}min")
    if engine == "essentia":
        pairs = scan_essentia(file, step, window, chunk_min, sr=44100)
    else:
        pairs = scan_file(file, step, window, chunk_min, sr, start_bpm)

    csv_path = file.with_suffix(file.suffix + ".bpm.csv")
    png_path = file.with_suffix(file.suffix + ".bpm.png")
    write_csv(pairs, csv_path)
    plot(pairs, png_path, f"BPM over time ({engine}) — {file.name}")
    print(f"wrote CSV: {csv_path}")
    print(f"{len(pairs)} estimates over {fmt_ts(pairs[-1][0] if pairs else 0)}")
    if pairs:
        bpms_only = [p[1] for p in pairs]
        print(f"BPM min/median/max: {min(bpms_only):.1f} / {np.median(bpms_only):.1f} / {max(bpms_only):.1f}")
    return csv_path
