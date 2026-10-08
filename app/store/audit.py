"""Write audit events in the caller's transaction (SPEC §7: ``hash = sha256(prev_hash + event)``). TR-SEC-03.

``details`` must hold IDs and counts only (CLAUDE.md rule 2). Phase 3 adds chain verification, export and
user attribution; the column layout is already the Phase 3 one.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.store.models.audit import AuditEvent

GENESIS = "0" * 64


def _canonical(ts: str, user_id: str | None, action: str, target: tuple[str, str], details: str) -> str:
    return json.dumps(
        {"ts": ts, "user_id": user_id, "action": action, "target": list(target), "details": details},
        sort_keys=True,
        separators=(",", ":"),
    )


def event_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def append_event(
    session: Session,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> AuditEvent:
    last = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).first()
    prev = last.hash if last else GENESIS
    ts = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    body = json.dumps(details or {}, sort_keys=True, separators=(",", ":"))
    event = AuditEvent(
        ts=ts,
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details_json=body,
        prev_hash=prev,
        hash=event_hash(prev, _canonical(ts, user_id, action, (target_type, target_id), body)),
    )
    session.add(event)
    session.flush()
    return event


def chain_ok(session: Session) -> bool:
    prev = GENESIS
    for ev in session.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        canonical = _canonical(ev.ts, ev.user_id, ev.action, (ev.target_type, ev.target_id), ev.details_json)
        if ev.prev_hash != prev or ev.hash != event_hash(prev, canonical):
            return False
        prev = ev.hash
    return True
