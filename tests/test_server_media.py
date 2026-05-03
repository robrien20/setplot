"""Media router: audio with Range, thumbnail, status passthrough."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from setplot import store
from setplot.pipeline import ingest as ingest_mod
from setplot.server.app import create_app

from .conftest import FIXTURES


def _make_client(tmp_path, monkeypatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    sid = ingest_mod.ingest_local(FIXTURES / "clip30.mp3", root=tmp_path)
    return TestClient(create_app()), sid


def test_audio_full_response(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    r = client.get(f"/api/sets/{sid}/audio")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "audio/mpeg"
    assert int(r.headers.get("content-length", "0")) > 100_000


def test_audio_range_response(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    full = client.get(f"/api/sets/{sid}/audio").content
    total = len(full)

    # Mid-file range
    r = client.get(f"/api/sets/{sid}/audio", headers={"Range": "bytes=1024-4095"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 1024-4095/{total}"
    assert r.headers["content-length"] == "3072"
    assert r.content == full[1024:4096]

    # Open-ended range
    r2 = client.get(f"/api/sets/{sid}/audio", headers={"Range": f"bytes={total - 100}-"})
    assert r2.status_code == 206
    assert r2.content == full[total - 100 :]


def test_audio_invalid_range_returns_416(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    r = client.get(f"/api/sets/{sid}/audio", headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


def test_thumbnail_404_when_missing(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    r = client.get(f"/api/sets/{sid}/thumbnail")
    assert r.status_code == 404


def test_status_json_passthrough(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    r = client.get(f"/api/sets/{sid}/status.json")
    assert r.status_code == 200
    assert r.json()["steps"]["ingest"] == "done"


def test_bpm_json_404_until_analysed(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    r = client.get(f"/api/sets/{sid}/bpm.json")
    assert r.status_code == 404


def test_bpm_json_present_after_analysis(tmp_path, monkeypatch):
    client, sid = _make_client(tmp_path, monkeypatch)
    # Stub a bpm.json directly — orchestrator integration is covered elsewhere.
    p = store.set_dir(sid, root=tmp_path) / "bpm.json"
    p.write_text(json.dumps({"step_s": 5.0, "data": [[0.0, 130.0]]}))
    r = client.get(f"/api/sets/{sid}/bpm.json")
    assert r.status_code == 200
    assert r.json()["data"] == [[0.0, 130.0]]
