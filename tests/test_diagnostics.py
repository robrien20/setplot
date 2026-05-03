"""``setplot doctor`` checks."""

from __future__ import annotations

import shutil

import pytest
from typer.testing import CliRunner

from setplot import diagnostics
from setplot.cli import app


def test_check_ffmpeg_handles_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    r = diagnostics.check_ffmpeg()
    assert r.ok is False
    assert "not found" in r.detail
    assert r.hint  # remediation hint present


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed locally")
def test_check_ffmpeg_passes_when_installed():
    r = diagnostics.check_ffmpeg()
    assert r.ok is True
    assert "ffmpeg version" in r.detail.lower() or "ffmpeg" in r.detail


def test_check_yt_dlp_imports():
    # yt-dlp is a runtime dep; should always be importable.
    r = diagnostics.check_yt_dlp()
    assert r.ok is True
    assert "library importable" in r.detail


def test_check_acr_creds_all_unset(monkeypatch):
    for k in ("ACR_HOST", "ACR_ACCESS_KEY", "ACR_ACCESS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    r = diagnostics.check_acr_creds()
    assert r.ok is False
    assert "ACR_HOST" in r.detail


def test_check_acr_creds_all_set(monkeypatch):
    monkeypatch.setenv("ACR_HOST", "x")
    monkeypatch.setenv("ACR_ACCESS_KEY", "y")
    monkeypatch.setenv("ACR_ACCESS_SECRET", "z")
    r = diagnostics.check_acr_creds()
    assert r.ok is True


def test_check_data_dir_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path / "subdir"))
    r = diagnostics.check_data_dir()
    assert r.ok is True
    assert "writable" in r.detail


def test_doctor_cli_passes_when_everything_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACR_HOST", "x")
    monkeypatch.setenv("ACR_ACCESS_KEY", "y")
    monkeypatch.setenv("ACR_ACCESS_SECRET", "z")
    # Force every system-binary check green.
    monkeypatch.setattr(
        diagnostics,
        "_check_binary",
        lambda name, args: diagnostics.CheckResult(name=name, ok=True, detail=f"/fake/{name} 1.0"),
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "All checks passed" in result.output


def test_doctor_cli_fails_with_exit1_when_any_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    for k in ("ACR_HOST", "ACR_ACCESS_KEY", "ACR_ACCESS_SECRET"):
        monkeypatch.delenv(k, raising=False)
    # Force binary checks to fail too.
    monkeypatch.setattr(
        diagnostics,
        "_check_binary",
        lambda name, args: diagnostics.CheckResult(name=name, ok=False, detail="missing", hint="hint"),
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "failed" in result.output
