"""Update-check helper. Network access is mocked everywhere."""

from __future__ import annotations

import datetime
import json

from setplot import _update


def test_is_newer_simple():
    assert _update._is_newer("0.2.0", "0.1.9")
    assert _update._is_newer("1.0.0", "0.99.99")
    assert not _update._is_newer("0.1.0", "0.1.0")
    assert not _update._is_newer("0.1.0", "0.2.0")
    # Pre-release suffix on latest still parses dotted-int prefix.
    assert _update._is_newer("0.2.0a1", "0.1.0")


def test_is_newer_handles_garbage():
    assert not _update._is_newer("garbage", "0.1.0")
    assert not _update._is_newer("0.1.0", "garbage")


def test_check_disabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SETPLOT_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(_update, "_fetch_latest_version", lambda repo: "9.9.9")
    assert _update.check("0.1.0") is None


def test_check_returns_hint_when_newer(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETPLOT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_update, "_fetch_latest_version", lambda repo: "0.2.0")
    hint = _update.check("0.1.0")
    assert hint is not None
    assert "0.2.0" in hint
    assert "uv tool upgrade setplot" in hint


def test_check_returns_none_when_current(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETPLOT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_update, "_fetch_latest_version", lambda repo: "0.1.0")
    assert _update.check("0.1.0") is None


def test_check_uses_cache_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETPLOT_NO_UPDATE_CHECK", raising=False)

    # Pre-populate the cache with a fresh entry; if anything tries to hit the
    # network we'll explode.
    cache = tmp_path / ".update-check.json"
    cache.write_text(
        json.dumps(
            {
                "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "latest_version": "5.0.0",
            }
        )
    )

    def boom(_repo):
        raise AssertionError("network was hit even though cache is fresh")

    monkeypatch.setattr(_update, "_fetch_latest_version", boom)
    hint = _update.check("0.1.0")
    assert hint is not None and "5.0.0" in hint


def test_check_refreshes_when_cache_is_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETPLOT_NO_UPDATE_CHECK", raising=False)

    stale = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=48)).isoformat()
    (tmp_path / ".update-check.json").write_text(json.dumps({"checked_at": stale, "latest_version": "0.0.5"}))

    monkeypatch.setattr(_update, "_fetch_latest_version", lambda repo: "0.2.0")
    hint = _update.check("0.1.0")
    assert hint is not None
    assert "0.2.0" in hint  # used the fresh value, not the stale cache


def test_check_silent_on_network_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETPLOT_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(_update, "_fetch_latest_version", lambda repo: None)
    assert _update.check("0.1.0") is None
