"""Job progress events, stored so a browser can replay them over SSE (SPEC §8 progress ≥ every 2 s).
TR-ELIG-14, TR-ELIG-15, TR-PERF-01.

Payloads carry job/record IDs, folder keys and counts only, never a file name, path or folder name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.context import ServiceContext, now_iso
from app.store.repos import jobs as jobs_repo

TYPES = frozenset(
    {"indexed", "record_start", "record_ready", "record_excluded", "record_failed", "progress", "finished",
     "cancelled", "error"}
)  # fmt: skip
END = frozenset({"finished", "cancelled", "error"})


@dataclass(frozen=True)
class Event:
    seq: int
    type: str
    data: dict[str, Any]


def emit(ctx: ServiceContext, job_id: str, type_: str, payload: dict[str, Any]) -> int:
    if type_ not in TYPES:
        raise ValueError(f"unknown job event type {type_!r}")
    with ctx.db.transaction() as s:
        return jobs_repo.add_event(s, job_id, type_, {"job_id": job_id, **payload}, now_iso())


def since(ctx: ServiceContext, job_id: str, seq: int) -> list[Event]:
    with ctx.db.session() as s:
        rows = jobs_repo.events_after(s, job_id, seq)
        return [Event(r.seq, r.type, json.loads(r.payload_json)) for r in rows]
