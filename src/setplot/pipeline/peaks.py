"""Pre-compute waveform peaks via bbc/audiowaveform for the streaming viewer.

We pass ``-z 256 -b 8``: 256 input samples per peak, 8 bits per peak. That's the
sweet spot for long DJ sets — small enough that even a 7-hour set's peaks.json
ships in well under 5 MB, dense enough that wavesurfer.js renders sharp peaks
when zoomed in via its Timeline plugin.

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

    out = store.step_output_path(set_id, "peaks", root=root)
    cmd = [
        "audiowaveform",
        "-i",
        str(src),
        "-o",
        str(out),
        "-z",
        str(samples_per_pixel),
        "-b",
        str(bits),
        "--output-format",
        "json",
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out
