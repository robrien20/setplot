"""Pre-compute waveform peaks via bbc/audiowaveform for the streaming viewer.

We pass ``-z 256 -b 8``: 256 input samples per peak, 8 bits per peak. That's the
sweet spot for long DJ sets — small enough that even a 7-hour set's peaks.json
ships in well under 5 MB, dense enough that wavesurfer.js renders sharp peaks
when zoomed in.

We always pipe the source through ``ffmpeg`` first to a mono 22050 Hz WAV
stream that audiowaveform reads on stdin. audiowaveform's native readers cover
only mp3 / wav / flac / ogg-vorbis — m4a (AAC-in-MP4) and mp4 video containers
fail with "Unknown file format", which is exactly what yt-dlp produces.
ffmpeg as a front end sidesteps the per-format support gap.

audiowaveform must be installed separately:
  - macOS: ``brew install audiowaveform``
  - Linux: ``apt-get install audiowaveform``
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from setplot import store


class AudioWaveformMissingError(RuntimeError):
    """Raised when the ``audiowaveform`` binary isn't on PATH."""


def audiowaveform_available() -> bool:
    return shutil.which("audiowaveform") is not None


def make_peaks(
    set_id: str,
    *,
    root: Path | None = None,
    samples_per_pixel: int = 256,
    bits: int = 8,
    sample_rate: int = 22050,
) -> Path:
    """Run ffmpeg → audiowaveform on the set's source media; return the peaks.json path."""
    if not audiowaveform_available():
        raise AudioWaveformMissingError(
            "audiowaveform not on PATH. Install with: "
            "`brew install audiowaveform` (macOS) or `apt-get install audiowaveform` (Debian/Ubuntu)."
        )
    src = store.find_source(set_id, root=root)
    if src is None:
        raise FileNotFoundError(f"no source.* file in {store.set_dir(set_id, root=root)}")

    out = store.step_output_path(set_id, "peaks", root=root)

    ffmpeg_cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        "-",
    ]
    aw_cmd = [
        "audiowaveform",
        "--input-format",
        "wav",
        "-i",
        "-",
        "-o",
        str(out),
        "-z",
        str(samples_per_pixel),
        "-b",
        str(bits),
        "--output-format",
        "json",
    ]
    ff = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        aw = subprocess.run(aw_cmd, stdin=ff.stdout, capture_output=True, check=False)
    finally:
        # Closing the pipe lets ffmpeg notice the consumer exited and shut down cleanly.
        if ff.stdout is not None:
            ff.stdout.close()
        ff.wait()
    if aw.returncode != 0:
        # Surface ffmpeg's stderr too — it's where decode errors show up.
        ff_err = (ff.stderr.read().decode("utf-8", "replace").strip() if ff.stderr else "").strip()
        aw_err = aw.stderr.decode("utf-8", "replace").strip()
        raise subprocess.CalledProcessError(
            aw.returncode,
            aw_cmd,
            output=aw.stdout,
            stderr=(aw_err + ("\nffmpeg: " + ff_err if ff_err else "")).encode("utf-8"),
        )
    return out
