"""Pre-compute waveform peaks via bbc/audiowaveform for the streaming viewer.

Defaults: ``-z 128 -b 16`` — 128 input samples per peak (~172 peaks/sec at
22050 Hz, 2× the BBC default density), 16 bits per peak (256× the dynamic
range of the 8-bit packing). Net file size for an 88-min DJ set is ~10 MB,
which the streaming viewer can pull in one fetch and feed to wavesurfer.js
for a smooth filled-waveform render.

If the source isn't natively readable by audiowaveform (mp4 video / m4a AAC),
we lean on the ``_decode`` helper to materialise a cached WAV next to the
source first. bpm + key share the same cache, so the ffmpeg cost is paid once.

audiowaveform must be installed separately:
  - macOS: ``brew install audiowaveform``
  - Linux: ``apt-get install audiowaveform``
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from setplot import store
from setplot.pipeline._decode import ensure_decoded_wav


class AudioWaveformMissingError(RuntimeError):
    """Raised when the ``audiowaveform`` binary isn't on PATH."""


def audiowaveform_available() -> bool:
    return shutil.which("audiowaveform") is not None


def make_peaks(
    set_id: str,
    *,
    root: Path | None = None,
    samples_per_pixel: int = 128,
    bits: int = 16,
    sample_rate: int = 22050,
) -> Path:
    """Run audiowaveform on the set's source media; return the peaks.json path."""
    if not audiowaveform_available():
        raise AudioWaveformMissingError(
            "audiowaveform not on PATH. Install with: "
            "`brew install audiowaveform` (macOS) or `apt-get install audiowaveform` (Debian/Ubuntu)."
        )
    src = store.find_source(set_id, root=root)
    if src is None:
        raise FileNotFoundError(f"no source.* file in {store.set_dir(set_id, root=root)}")

    decoded = ensure_decoded_wav(src, sample_rate)
    out = store.step_output_path(set_id, "peaks", root=root)
    subprocess.run(
        [
            "audiowaveform",
            "-i",
            str(decoded),
            "-o",
            str(out),
            "-z",
            str(samples_per_pixel),
            "-b",
            str(bits),
            "--output-format",
            "json",
        ],
        check=True,
        capture_output=True,
    )
    return out
