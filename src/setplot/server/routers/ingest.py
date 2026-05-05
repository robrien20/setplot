"""Ingest + SSE endpoints.

POST ``/api/ingest`` accepts ``{target, skip_peaks?, skip_fingerprint?, key_engine?}``
and kicks off the orchestrator in a thread (orchestrator is CPU-heavy and sync).
The endpoint returns ``{job_id}`` immediately. Clients open
``GET /api/jobs/{job_id}/stream`` for live ``text/event-stream`` updates that
the orchestrator publishes via the in-memory ``JobBus``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from setplot.pipeline import ingest as ingest_mod
from setplot.pipeline import orchestrator
from setplot.server.events import bus

router = APIRouter(tags=["ingest"])


class IngestRequest(BaseModel):
    target: str
    skip_peaks: bool = False
    skip_fingerprint: bool = False
    key_engine: str = "essentia"


class ReanalyzeRequest(BaseModel):
    steps: list[str]
    key_engine: str = "essentia"


def _format_sse(event: dict[str, Any], event_type: str | None = None) -> bytes:
    """RFC: each SSE message is ``event: <type>\\ndata: <utf-8>\\n\\n``."""
    lines = []
    if event_type:
        lines.append(f"event: {event_type}")
    lines.append("data: " + json.dumps(event, ensure_ascii=False))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _run_pipeline_for_job(job_id: str, req: IngestRequest) -> None:
    """Synchronous worker that runs ingest + orchestrator and publishes events.

    Lives in a worker thread (FastAPI BackgroundTasks runs sync funcs in a
    threadpool); ``bus.publish`` is thread-safe.
    """
    try:
        bus.publish(job_id, {"step": "ingest", "state": "running"})
        set_id = ingest_mod.ingest(req.target)
        bus.publish(job_id, {"step": "ingest", "state": "done", "set_id": set_id})

        skip = tuple(s for s, on in (("fingerprint", req.skip_fingerprint), ("peaks", req.skip_peaks)) if on)

        def cb(ev: dict[str, Any]) -> None:
            bus.publish(job_id, {**ev, "set_id": set_id})

        orchestrator.analyze(set_id, key_engine=req.key_engine, skip=skip, event_cb=cb)
        bus.publish(job_id, {"step": "all", "state": "done", "set_id": set_id})
    except Exception as exc:
        bus.publish(job_id, {"step": "ingest", "state": "failed", "error": str(exc)})
    finally:
        bus.close_job(job_id)


def _run_reanalyze_for_job(job_id: str, set_id: str, req: ReanalyzeRequest) -> None:
    try:
        def cb(ev: dict[str, Any]) -> None:
            bus.publish(job_id, {**ev, "set_id": set_id})

        orchestrator.reanalyze_steps(set_id, req.steps, key_engine=req.key_engine, event_cb=cb)
        bus.publish(job_id, {"step": "all", "state": "done", "set_id": set_id})
    except Exception as exc:
        bus.publish(job_id, {"step": "?", "state": "failed", "error": str(exc)})
    finally:
        bus.close_job(job_id)


@router.post("/sets/{set_id}/analyze")
async def reanalyze(set_id: str, req: ReanalyzeRequest, background: BackgroundTasks) -> dict[str, str]:
    job_id = bus.create_job()
    background.add_task(_run_reanalyze_for_job, job_id, set_id, req)
    return {"job_id": job_id}


@router.post("/ingest")
async def ingest(req: IngestRequest, background: BackgroundTasks) -> dict[str, str]:
    job_id = bus.create_job()
    background.add_task(_run_pipeline_for_job, job_id, req)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str) -> StreamingResponse:
    async def gen():
        # Keep-alive comment up-front so EventSource fires `open` before the first
        # real event (otherwise the browser can sit waiting for a few seconds).
        yield b": connected\n\n"
        async for event in bus.subscribe(job_id):
            yield _format_sse(event)
            await asyncio.sleep(0)  # let other tasks run
        yield _format_sse({"step": "stream", "state": "closed"}, event_type="close")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            "Connection": "keep-alive",
        },
    )
