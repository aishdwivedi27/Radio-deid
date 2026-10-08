"""Audit log view, CSV export and chain verification (SPEC §2, §7). TR-SEC-03.

Custodian and Auditor only (SPEC §2; D-027). The export adds a ``username`` column read from the users
table at export time; it is not part of the hashed event. Each export is audited (``export.created``).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

from app.auth.errors import Actor
from app.services.auth import access
from app.services.context import ServiceContext
from app.store import audit
from app.store.audit import AuditFilter
from app.store.models.audit import AuditEvent
from app.store.repos import users as repo

__all__ = ["AuditFilter", "export_csv", "list_events", "verify"]

CSV_COLUMNS = (
    "id", "ts", "user_id", "username", "action", "target_type", "target_id", "details_json", "prev_hash",
    "hash",
)  # fmt: skip
MAX_PAGE = 500


def _usernames(ctx: ServiceContext) -> dict[str, str]:
    with ctx.db.session() as s:
        return {u.id: u.username for u in repo.all_users(s)}


def _row(ev: AuditEvent, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": ev.id, "ts": ev.ts, "user_id": ev.user_id, "username": names.get(ev.user_id or "", ""),
        "action": ev.action, "target_type": ev.target_type, "target_id": ev.target_id,
        "details_json": ev.details_json, "prev_hash": ev.prev_hash, "hash": ev.hash,
    }  # fmt: skip


def list_events(
    ctx: ServiceContext, actor: Actor, f: audit.AuditFilter, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    access.check(ctx, actor, "audit.view", "audit.list")
    names = _usernames(ctx)
    with ctx.db.session() as s:
        events = audit.list_events(s, f, max(1, min(limit, MAX_PAGE)), max(0, offset))
        return [_row(ev, names) for ev in events]


def export_csv(ctx: ServiceContext, actor: Actor, f: audit.AuditFilter) -> Iterator[str]:
    """Audit first, then stream the rows (the export event itself is included when the range covers it)."""
    access.check(ctx, actor, "audit.view", "audit.export")
    filters = {k: v for k, v in f.__dict__.items() if v}
    with ctx.db.transaction() as s:
        details = {"filters": sorted(filters)}
        audit.append_event(s, "export.created", "audit_log", "csv", details, actor.user_id)
    names = _usernames(ctx)

    def rows() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        with ctx.db.session() as s:
            for ev in audit.iter_events(s, f):
                writer.writerow(_row(ev, names))
                if buf.tell() > 64_000:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()

    return rows()


def verify(ctx: ServiceContext, actor: Actor) -> audit.ChainResult:
    access.check(ctx, actor, "audit.view", "audit.verify")
    with ctx.db.session() as s:
        return audit.verify_chain(s)
