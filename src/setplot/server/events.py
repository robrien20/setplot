"""In-memory job pub/sub used to bridge sync orchestrator callbacks to async
SSE consumers.

Each ``POST /api/ingest`` creates a fresh job with a uuid; the orchestrator
calls ``publish(job_id, event)`` from its sync ``event_cb``; the SSE endpoint
``subscribe(job_id)``-s and yields events until a sentinel arrives.

Single-process only — no Redis, no Celery. That's fine: SetPlot is a local-first
single-user tool.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from typing import Any

# Sentinel placed on a job's queue when the orchestrator finishes (or errors)
# so the SSE consumer can close the stream cleanly.
_DONE_SENTINEL = {"__bus_close__": True}


class JobBus:
    """One asyncio queue per running job. Survives only as long as the process."""

    def __init__(self) -> None:
        self._jobs: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------- lifecycle -------
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running event loop so sync threads can schedule into it."""
        self._loop = loop

    def create_job(self) -> str:
        jid = secrets.token_urlsafe(8)
        self._jobs[jid] = asyncio.Queue()
        return jid

    def discard_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    # ------- publish (callable from sync threads) -------
    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        q = self._jobs.get(job_id)
        if q is None:
            return
        if self._loop is None:
            # Fallback for unit tests where we never attached a running loop.
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
            return
        self._loop.call_soon_threadsafe(q.put_nowait, event)

    def close_job(self, job_id: str) -> None:
        """Mark a job as complete so subscribers exit their loop."""
        self.publish(job_id, _DONE_SENTINEL)

    # ------- subscribe (async consumer) -------
    async def subscribe(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        q = self._jobs.get(job_id)
        if q is None:
            return
        while True:
            event = await q.get()
            if event is _DONE_SENTINEL:
                self.discard_job(job_id)
                return
            yield event


# A single, app-wide instance is fine for the local-first model.
bus = JobBus()
