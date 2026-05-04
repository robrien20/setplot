"""One-shot decode of source media to a libsndfile-readable WAV.

mp4 / m4a / webm / mov sources can't be read by libsndfile (and therefore
neither by ``soundfile`` nor by ``audiowaveform``'s native readers). librosa
falls back to audioread for these, which spawns a fresh ffmpeg per chunk —
slow + noisy.

We sidestep that: ffmpeg the file once, mono-mix at the analysis sample rate,
write a cached ``.work-{sr}.wav`` next to the source. bpm/key/peaks then read
the cache via libsndfile directly. Per-chunk reads become near-free, and the
audioread deprecation warnings stop firing entirely.

Cache lives in the per-set dir, so ``setplot rm <id>`` cleans it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# soundfile 0.11+ (we ship 0.13) reads these natively via libsndfile.
# Anything outside this set goes through the ffmpeg pre-decode path.
_LIBSNDFILE_NATIVE_EXTS = frozenset({".wav", ".flac", ".ogg", ".oga", ".mp3"})


def ensure_decoded_wav(src: Path, sr: int = 22050) -> Path:
    """Return a path that ``soundfile`` / ``audiowaveform`` can read directly.

    For natively-supported formats this is a no-op (returns ``src``). For
    everything else we decode via ffmpeg to a cached
    ``<src.parent>/.work-{sr}.wav`` and return that. Idempotent: second and
    subsequent calls hit the cache.
    """
    src = Path(src)
    if src.suffix.lower() in _LIBSNDFILE_NATIVE_EXTS:
        return src
    cache = src.with_name(f".work-{sr}.wav")
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(cache),
        ],
        check=True,
        capture_output=True,
    )
    return cache
