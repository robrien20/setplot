"""Library router via FastAPI TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from setplot import store
from setplot.pipeline import ingest as ingest_mod
from setplot.server.app import create_app

from .conftest import FIXTURES


def _make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def test_get_sets_empty(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/sets")
    assert r.status_code == 200
    assert r.json() == {"sets": []}


def test_get_sets_lists_after_ingest(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    r = client.get("/api/sets")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sets"]) == 1
    card = body["sets"][0]
    assert card["set_id"] == sid
    assert card["title"] == "clip30"
    assert card["total_steps"] == len(store.STEPS)
    assert card["completed_steps"] == 1  # only ingest is done


def test_get_set_detail(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    r = client.get(f"/api/sets/{sid}")
    assert r.status_code == 200
    card = r.json()
    assert card["set_id"] == sid
    assert "steps" in card
    assert card["steps"]["ingest"] == "done"


def test_get_set_404(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/sets/nope-deadbeef")
    assert r.status_code == 404
