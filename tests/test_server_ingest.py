"""POST /api/ingest + SSE stream end-to-end via TestClient."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from setplot.server.app import create_app

from .conftest import FIXTURES


def _make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SETPLOT_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for chunk in body.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_ingest_local_file_emits_step_events(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    payload = {
        "target": str(FIXTURES / "clip30.mp3"),
        "skip_peaks": True,
        "skip_fingerprint": True,
        "key_engine": "librosa",
    }
    r = client.post("/api/ingest", json=payload)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id

    # TestClient buffers streaming responses; we just collect everything that arrives.
    with client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        assert resp.status_code == 200
        body = resp.read().decode("utf-8")
    events = _parse_sse_events(body)

    states = {(e.get("step"), e.get("state")) for e in events}
    # Either the orchestrator chose its own steps (with peaks/fp skipped) and we see them,
    # or the worker raced ahead and we caught the final batch — be lenient.
    assert ("ingest", "done") in states
    assert ("bpm", "done") in states
    assert ("key", "done") in states
    assert ("all", "done") in states
    # set_id is propagated on every post-ingest event.
    set_ids = {e.get("set_id") for e in events if e.get("set_id")}
    assert len(set_ids) == 1
