"""Smoke-test the typer CLI so we know the entry point + subcommands wire up."""

from __future__ import annotations

from typer.testing import CliRunner

from setplot import __version__
from setplot.cli import app


def test_cli_version_flag():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


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
