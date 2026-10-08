"""Append-only, hash-chained audit log (SPEC §7: ``hash = sha256(prev_hash + event)``). TR-SEC-03.

Events are written in the caller's transaction. ``details`` holds IDs, codes and counts only, never
identifying data (CLAUDE.md rule 2). Actions come from the closed ``EVENTS`` registry. Migration 0002 adds
triggers that refuse UPDATE and DELETE on ``audit_events``. ``verify_chain`` reports the first broken link.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.store.models.audit import AuditEvent

GENESIS = "0" * 64

# SPEC §7 events (TR-SEC-03), then events this app adds. Some are emitted by later phases (jobs, review
# mode, licensees, gate, releases, re-identification tests, breaches, exports of releases, key backup and
# rotation); they are registered now so the vocabulary is fixed in one place.
SPEC_EVENTS: frozenset[str] = frozenset(
    {
        "auth.login_success", "auth.login_failure", "auth.logout",
        "user.created", "user.changed", "user.disabled",
        "custodian.granted",
        "settings.changed",
        "ethics.config_changed",
        "preapproval.refused_file",
        "job.started", "job.cancelled", "job.finished",
        "exclusion.recorded",
        "record.approved", "record.rejected", "record.excluded", "record.finalised", "record.withdrawn",
        "list.imported",
        "review_mode.changed",
        "licensee.changed",
        "gate.passed", "gate.failed",
        "release.created",
        "reid.recorded",
        "breach.opened", "breach.ec_notified", "breach.closed",
        "export.created",
        "key.backed_up", "key.rotated",
        "sod.toggled",
    }
)  # fmt: skip
APP_EVENTS: frozenset[str] = frozenset(
    {
        "access.denied",  # a permission check failed (T17)
        "setup.completed",
        "user.enabled", "user.unlocked", "user.password_reset", "user.password_changed", "user.admin_reset",
        "key.created", "key.viewed", "key.backup_requested", "key.rotate_requested",
        "reconcile.run",
        "exclusion.verified",  # SPEC §6.4 skip spot-check (Phase 4)
    }
)  # fmt: skip
EVENTS: frozenset[str] = SPEC_EVENTS | APP_EVENTS


class AuditError(ValueError):
    """An unknown action or a malformed event. Messages carry action names only."""


def _canonical(ts: str, user_id: str | None, action: str, target: tuple[str, str], details: str) -> str:
    return json.dumps(
        {"ts": ts, "user_id": user_id, "action": action, "target": list(target), "details": details},
        sort_keys=True,
        separators=(",", ":"),
    )


def event_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def _event_canonical(ev: AuditEvent) -> str:
    return _canonical(ev.ts, ev.user_id, ev.action, (ev.target_type, ev.target_id), ev.details_json)


def append_event(
    session: Session,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> AuditEvent:
    if action not in EVENTS:
        raise AuditError(f"unknown audit action {action!r}")
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


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    checked: int
    first_bad_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checked": self.checked, "first_bad_id": self.first_bad_id}


def verify_chain(session: Session) -> ChainResult:
    """Walk the chain from the genesis; the first event whose link or hash does not match is reported.
    Known limit: rows removed from the end of the chain leave a valid shorter chain."""
    prev, n = GENESIS, 0
    for ev in session.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        if ev.prev_hash != prev or ev.hash != event_hash(prev, _event_canonical(ev)):
            return ChainResult(False, n, ev.id)
        prev, n = ev.hash, n + 1
    return ChainResult(True, n)


def chain_ok(session: Session) -> bool:
    return verify_chain(session).ok


@dataclass(frozen=True)
class AuditFilter:
    ts_from: str | None = None  # ISO date or datetime, inclusive
    ts_to: str | None = None  # ISO date or datetime; a bare date includes the whole day
    user_id: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None


def _filtered(f: AuditFilter) -> Select[AuditEvent]:
    q = select(AuditEvent)
    if f.ts_from:
        q = q.where(AuditEvent.ts >= f.ts_from)
    if f.ts_to:
        q = q.where(AuditEvent.ts <= (f.ts_to + "T99" if len(f.ts_to) == 10 else f.ts_to))
    for column, value in (
        (AuditEvent.user_id, f.user_id),
        (AuditEvent.action, f.action),
        (AuditEvent.target_type, f.target_type),
        (AuditEvent.target_id, f.target_id),
    ):
        if value:
            q = q.where(column == value)
    return q


def list_events(session: Session, f: AuditFilter, limit: int = 100, offset: int = 0) -> list[AuditEvent]:
    q = _filtered(f).order_by(AuditEvent.id.desc()).limit(limit).offset(offset)
    return list(session.scalars(q))


def iter_events(session: Session, f: AuditFilter) -> Iterator[AuditEvent]:
    yield from session.scalars(_filtered(f).order_by(AuditEvent.id))
