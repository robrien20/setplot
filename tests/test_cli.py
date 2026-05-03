"""Smoke-test the typer CLI so we know the entry point + subcommands wire up."""

from __future__ import annotations

from typer.testing import CliRunner

from setplot import __version__
from setplot.cli import app


def test_cli_version_flag(monkeypatch):
    monkeypatch.setenv("SETPLOT_NO_UPDATE_CHECK", "1")  # keep --version offline + fast
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_version_includes_upgrade_hint(tmp_path, monkeypatch):
    """When the update checker reports a newer release, --version surfaces it."""
    from setplot import _update

    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETPLOT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_update, "_fetch_latest_version", lambda repo: "9.9.9")
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "9.9.9" in result.output
    assert "uv tool upgrade setplot" in result.output


def test_cli_help_lists_all_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("bpm", "key", "identify", "plot", "serve"):
        assert cmd in result.output


def test_cli_bpm_help_shows_options():
    result = CliRunner().invoke(app, ["bpm", "--help"])
    assert result.exit_code == 0
    assert "--step" in result.output
    assert "--window" in result.output


def test_cli_help_lists_phase2_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("import", "list", "rm"):
        assert cmd in result.output


def test_cli_import_list_rm_round_trip(tmp_path, monkeypatch):
    """Ingest a local file, list it, then rm it via the CLI."""
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    runner = CliRunner()

    fixture = "tests/fixtures/clip30.mp3"
    result = runner.invoke(
        app,
        ["import", fixture, "--no-analyze"],
    )
    assert result.exit_code == 0, result.output
    assert "ingested →" in result.output
    sid = result.output.strip().split()[-1]

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert sid in result.output

    result = runner.invoke(app, ["rm", sid])
    assert result.exit_code == 0
    assert "removed" in result.output

    # Removing again should fail with exit code 1.
    result = runner.invoke(app, ["rm", sid])
    assert result.exit_code == 1


def test_cli_list_empty_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["list"])
    assert result.exit_code == 0
    assert "no sets" in result.output
