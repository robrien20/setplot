"""Detect musical key (Camelot notation) over time for a long audio file.

Uses Essentia's `KeyExtractor` with the **edma** profile — a key-profile set
trained specifically on electronic dance music (Faraldo et al., 2016).
Internally it computes HPCP (Harmonic Pitch Class Profile), which is more
drum-robust than plain STFT chroma, then correlates against the EDM-tuned
templates. Significantly more accurate on club material than librosa's
Temperley-profile template matching.

Falls back to librosa + Temperley if essentia isn't available.
"""

from __future__ import annotations

import colorsys
import csv
import subprocess
import tempfile
import warnings
from pathlib import Path

import numpy as np

# Same audioread/libsndfile noise as bpm.py — librosa falls back chattily for
# mp4/m4a sources. Filter at module load.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"librosa\..*")
warnings.filterwarnings("ignore", category=UserWarning, module=r"librosa\..*")

try:
    import essentia
    import essentia.standard as es

    HAS_ESSENTIA = True
    # Essentia logs "No network created…" per per-window MonoLoader teardown.
    # Internal cleanup chatter, not actionable; quiet it.
    essentia.log.warningActive = False
    essentia.log.infoActive = False
except ImportError:
    HAS_ESSENTIA = False


PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PITCH_NAME_TO_PC = {name: i for i, name in enumerate(PITCH_NAMES)}
# Essentia emits enharmonic spellings — normalize.
ENHARMONICS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}

CAMELOT_MAJOR = {
    11: "1B",
    6: "2B",
    1: "3B",
    8: "4B",
    3: "5B",
    10: "6B",
    5: "7B",
    0: "8B",
    7: "9B",
    2: "10B",
    9: "11B",
    4: "12B",
}
CAMELOT_MINOR = {
    8: "1A",
    3: "2A",
    10: "3A",
    5: "4A",
    0: "5A",
    7: "6A",
    2: "7A",
    9: "8A",
    4: "9A",
    11: "10A",
    6: "11A",
    1: "12A",
}


def key_scale_to_camelot(key: str, scale: str) -> tuple[int, str, str]:
    """('F#', 'major') -> (6, 'maj', '2B')."""
    key = ENHARMONICS.get(key, key)
    pc = PITCH_NAME_TO_PC[key]
    mode = "maj" if scale.lower().startswith("maj") else "min"
    camelot = (CAMELOT_MAJOR if mode == "maj" else CAMELOT_MINOR)[pc]
    return pc, mode, camelot


def pc_mode_to_name(pc: int, mode: str) -> str:
    return f"{PITCH_NAMES[pc]} {'major' if mode == 'maj' else 'minor'}"


def pc_mode_to_camelot(pc: int, mode: str) -> str:
    return (CAMELOT_MAJOR if mode == "maj" else CAMELOT_MINOR)[pc]


# -----------------------------------------------------------------------------
# Essentia-based key extraction (preferred)
# -----------------------------------------------------------------------------
def extract_clip_wav(path: Path, offset_s: float, duration_s: float, out_path: str) -> None:
    """Slice a clip via ffmpeg — essentia wants PCM WAV at 44.1k."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            str(offset_s),
            "-i",
            str(path),
            "-t",
            str(duration_s),
            "-ac",
            "1",
            "-ar",
            "44100",
            "-f",
            "wav",
            "-acodec",
            "pcm_s16le",
            out_path,
        ],
        check=True,
    )


def scan_essentia(path: Path, step_s: float, window_s: float, duration: float) -> list:
    """Yield (t_abs, pc, mode, strength, strength) for each window."""
    if not HAS_ESSENTIA:
        raise RuntimeError("essentia is not installed; install setplot[essentia] or use engine='librosa'")

    from setplot.pipeline._decode import ensure_decoded_wav

    # Essentia's KeyExtractor expects 44.1 kHz; pre-decode + cache once so each
    # per-window ffmpeg slice becomes a fast wav-to-wav copy instead of a fresh
    # mp4 decode pass.
    path = ensure_decoded_wav(path, sr=44100)

    rows: list = []
    # Essentia: create one KeyExtractor and reuse. profileType='edma' is tuned for EDM.
    key_ext = es.KeyExtractor(profileType="edma")
    with tempfile.TemporaryDirectory() as tmpdir:
        clip_path = f"{tmpdir}/clip.wav"
        t = 0.0
        n_windows = int(duration / step_s) + 1
        i = 0
        while t + window_s <= duration:
            extract_clip_wav(path, t, window_s, clip_path)
            audio = es.MonoLoader(filename=clip_path)()
            key_note, scale, strength = key_ext(audio)
            try:
                pc, mode, _cam = key_scale_to_camelot(key_note, scale)
                rows.append((t, pc, mode, float(strength), float(strength)))
            except KeyError:
                # Rare: essentia returns something unparseable
                pass
            i += 1
            if i % 60 == 0:
                print(f"  essentia t={t / 60:6.1f}min  ({i}/{n_windows})")
            t += step_s
    return rows


# -----------------------------------------------------------------------------
# Librosa fallback (Temperley profile correlation)
# -----------------------------------------------------------------------------
MAJOR_PROFILE = np.array([5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0])
MINOR_PROFILE = np.array([5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0])


def estimate_key_librosa(chroma_mean: np.ndarray) -> tuple[int, str, float, float]:
    scores = np.zeros(24)
    best = (-np.inf, 0, "maj")
    for pc in range(12):
        cor_maj = np.corrcoef(chroma_mean, np.roll(MAJOR_PROFILE, pc))[0, 1]
        cor_min = np.corrcoef(chroma_mean, np.roll(MINOR_PROFILE, pc))[0, 1]
        scores[pc] = cor_maj
        scores[pc + 12] = cor_min
        if cor_maj > best[0]:
            best = (cor_maj, pc, "maj")
        if cor_min > best[0]:
            best = (cor_min, pc, "min")
    sorted_scores = np.sort(scores)[::-1]
    margin = sorted_scores[0] - sorted_scores[1]
    return best[1], best[2], float(best[0]), float(margin)


def scan_librosa(path: Path, step_s: float, window_s: float, chunk_min: float, sr: int) -> list:
    import librosa

    from setplot.pipeline._decode import ensure_decoded_wav

    # Pre-decode mp4/m4a once (no-op for mp3/wav/flac); chunked librosa.load
    # then reads through libsndfile directly instead of the slow audioread path.
    path = ensure_decoded_wav(path, sr)
    duration = librosa.get_duration(path=str(path))
    chunk_s = chunk_min * 60
    overlap = window_s
    results: list = []
    t = 0.0
    while t < duration:
        this_len = min(chunk_s + overlap, duration - t)
        y, _ = librosa.load(str(path), sr=sr, mono=True, offset=t, duration=this_len)
        win = int(window_s * sr)
        step = int(step_s * sr)
        for start in range(0, len(y) - win + 1, step):
            clip = y[start : start + win]
            chroma = librosa.feature.chroma_cens(y=clip, sr=sr)
            pc, mode, corr, margin = estimate_key_librosa(chroma.mean(axis=1))
            abs_t = t + start / sr
            if results and abs_t <= results[-1][0] + step_s / 2:
                continue
            results.append((abs_t, pc, mode, corr, margin))
        print(f"  librosa chunk t={t / 60:6.1f}min  total {len(results)}")
        t += chunk_s
    return results


def fmt_ts(sec):
    t = max(0, int(sec))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "timestamp", "camelot", "key", "strength", "margin"])
        for t, pc, mode, strength, margin in rows:
            w.writerow(
                [
                    f"{t:.2f}",
                    fmt_ts(t),
                    pc_mode_to_camelot(pc, mode),
                    pc_mode_to_name(pc, mode),
                    f"{strength:.3f}",
                    f"{margin:.3f}",
                ]
            )


def camelot_rgb(camelot):
    hour = int(camelot[:-1])
    ring = camelot[-1]
    return colorsys.hsv_to_rgb((hour - 1) / 12, 0.75, 0.55 if ring == "A" else 0.85)


def plot_strip(rows, path, title):
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(22, 5), gridspec_kw={"height_ratios": [1.5, 1]})
    times = np.array([r[0] for r in rows]) / 3600
    colors = [camelot_rgb(pc_mode_to_camelot(r[1], r[2])) for r in rows]
    w = (times[1] - times[0]) if len(times) > 1 else 0.01
    ax1.bar(times, [1] * len(rows), width=w, color=colors, align="edge", edgecolor="none")
    ax1.set_yticks([])
    ax1.set_xlim(0, times[-1] + w)
    ax1.set_title(title)
    ax2.plot(times, [r[3] for r in rows], linewidth=0.5, label="key strength", color="#1a4480")
    ax2.set_xlabel("time (hours)")
    ax2.set_ylabel("confidence")
    ax2.legend(loc="upper right", fontsize=9)
    ax2.set_xlim(0, times[-1])
    ax2.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote plot: {path}")


def run(
    file: Path,
    *,
    step: float = 10.0,
    window: float = 48.0,
    chunk_min: float = 10.0,
    sr: int = 22050,
    engine: str = "essentia",
) -> Path:
    """Run key analysis end-to-end. Returns path to the CSV."""
    file = Path(file)
    if not file.exists():
        raise FileNotFoundError(file)

    if engine == "essentia" and not HAS_ESSENTIA:
        print("essentia not installed — falling back to librosa")
        engine = "librosa"

    print(f"file: {file}  engine={engine}  step={step}s  window={window}s")
    if engine == "essentia":
        import librosa as _librosa

        duration = _librosa.get_duration(path=str(file))
        rows = scan_essentia(file, step, window, duration)
    else:
        rows = scan_librosa(file, step, window, chunk_min, sr)

    csv_path = file.with_suffix(file.suffix + f".key_{engine}.csv")
    png_path = file.with_suffix(file.suffix + f".key_{engine}.png")
    write_csv(rows, csv_path)
    plot_strip(rows, png_path, f"Camelot key over time ({engine}) — {file.name}")
    print(f"wrote CSV: {csv_path}")

    from collections import Counter

    cam_counter = Counter(pc_mode_to_camelot(r[1], r[2]) for r in rows)
    print("\nTop 10 Camelot keys by duration:")
    for cam, n in cam_counter.most_common(10):
        print(f"  {cam:>3}  {n * step / 60:5.1f} min")
    if rows:
        avg_strength = np.mean([r[3] for r in rows])
        print(f"\nAvg key strength: {avg_strength:.3f}")
    return csv_path
