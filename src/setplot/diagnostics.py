"""``setplot doctor`` — verify system deps + ACR creds with platform-aware hints."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    hint: str = ""


def _platform_pkg_install_hint(pkg: str) -> str:
    if sys.platform == "darwin":
        return f"`brew install {pkg}`"
    if sys.platform.startswith("linux"):
        return f"`apt-get install {pkg}` (Debian/Ubuntu) or your distro's equivalent"
    return f"install {pkg} from your platform's package manager"


def _check_binary(name: str, version_args: list[str]) -> CheckResult:
    path = shutil.which(name)
    if not path:
        return CheckResult(
            name=name,
            ok=False,
            detail="not found on PATH",
            hint=_platform_pkg_install_hint(name),
        )
    try:
        out = subprocess.check_output([path, *version_args], stderr=subprocess.STDOUT, text=True, timeout=5)
        first = out.splitlines()[0].strip() if out else "(no version output)"
        return CheckResult(name=name, ok=True, detail=f"{path} — {first}")
    except (subprocess.SubprocessError, OSError) as e:
        return CheckResult(name=name, ok=False, detail=f"{path} but failed to run: {e}")


def check_ffmpeg() -> CheckResult:
    return _check_binary("ffmpeg", ["-version"])


def check_audiowaveform() -> CheckResult:
    r = _check_binary("audiowaveform", ["--version"])
    if not r.ok:
        # Override hint with the official BBC instructions for non-mac.
        if sys.platform.startswith("linux"):
            r.hint = "`sudo apt-get install audiowaveform` — needs Universe enabled on Ubuntu"
        elif sys.platform == "darwin":
            r.hint = "`brew install audiowaveform`"
    return r


def check_yt_dlp() -> CheckResult:
    """yt-dlp ships as a runtime dep, so we check the importable module rather than a CLI."""
    try:
        import yt_dlp
        from yt_dlp import version as yt_dlp_version

        version = getattr(yt_dlp_version, "__version__", None) or getattr(yt_dlp, "__version__", "?")
        return CheckResult(name="yt-dlp", ok=True, detail=f"library importable, version {version}")
    except (ImportError, AttributeError) as e:
        return CheckResult(
            name="yt-dlp",
            ok=False,
            detail=f"import failed: {e}",
            hint="reinstall: `uv tool upgrade setplot`",
        )


def check_acr_creds() -> CheckResult:
    """Resolve via pydantic-settings so a project ``.env`` counts the same as
    real env vars."""
    from setplot.config import get_settings

    s = get_settings()
    pairs = (
        ("ACR_HOST", s.acr_host),
        ("ACR_ACCESS_KEY", s.acr_access_key),
        ("ACR_ACCESS_SECRET", s.acr_access_secret),
    )
    missing = [name for name, val in pairs if not val]
    if not missing:
        return CheckResult(
            name="acr creds",
            ok=True,
            detail="ACR_HOST, ACR_ACCESS_KEY, ACR_ACCESS_SECRET all set",
        )
    return CheckResult(
        name="acr creds",
        ok=False,
        detail=f"missing: {', '.join(missing)}",
        hint="add to your shell init or a project .env. Get them at https://console.acrcloud.com",
    )


def check_data_dir() -> CheckResult:
    from setplot.config import get_settings

    p = get_settings().data_dir()
    try:
        p.mkdir(parents=True, exist_ok=True)
        # Probe writability with a temp file we immediately remove.
        probe = p / ".probe"
        probe.write_text("ok")
        probe.unlink()
        return CheckResult(name="data dir", ok=True, detail=f"{p} (writable)")
    except OSError as e:
        return CheckResult(
            name="data dir",
            ok=False,
            detail=f"{p} — {e}",
            hint="ensure the parent dir exists and is writable, or set SETPLOT_DATA_DIR to an alternative path",
        )


def run_checks() -> list[CheckResult]:
    return [
        check_data_dir(),
        check_ffmpeg(),
        check_audiowaveform(),
        check_yt_dlp(),
        check_acr_creds(),
    ]
