"""Server-Sent Events for job progress (SPEC §8: progress at least every 2 s). TR-PERF-01.

Events are read from the DB (``services.jobs.query``), so a reconnecting browser replays from
``Last-Event-ID`` and a restart loses nothing. When nothing new happens for 2 s a ``progress`` snapshot is
sent (no id, so replay is unaffected). The stream ends after ``finished``, ``cancelled`` or ``error``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.services.context import ServiceContext
from app.services.jobs import query
from app.services.jobs.events import END

POLL_S = 0.5
HEARTBEAT_S = 2.0


def message(type_: str, data: dict[str, Any], seq: int | None = None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {type_}\ndata: {json.dumps(data, sort_keys=True, separators=(',', ':'))}\n\n"


async def stream(ctx: ServiceContext, job_id: str, last_seq: int) -> AsyncIterator[str]:
    seq, quiet_since = last_seq, time.monotonic()
    while True:
        batch = await run_in_threadpool(query.events_since, ctx, job_id, seq)
        for ev in batch:
            seq = ev.seq
            yield message(ev.type, ev.data, ev.seq)
            if ev.type in END:
                return
        now = time.monotonic()
        if batch:
            quiet_since = now
        elif now - quiet_since >= HEARTBEAT_S:
            snapshot = await run_in_threadpool(query.progress, ctx, job_id)
            yield message("progress", snapshot)
            quiet_since = now
        await asyncio.sleep(POLL_S)
