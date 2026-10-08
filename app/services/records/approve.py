"""Review decisions on a pending version (SPEC §4, §6.3). TR-REV-03, TR-REV-04.

``review_decision`` is the entry point for people: it checks the ``records.review`` permission and, while
separation of duties is on (default), refuses a reviewer who started the record's job (SPEC §2, TR-ROLE-04,
criterion 3). ``record_decision`` is the state machine underneath. Phase 5 adds the mandatory checkboxes,
the approval note and the API.
"""

from __future__ import annotations

from app.auth.errors import Actor, AppError, not_found
from app.deid.record.finalrow import finding_codes
from app.deid.types import DeidError
from app.services.auth import access
from app.services.auth.settings import sod_enabled
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.repos import records as recs
from app.store.repos import settings as cfg

SOD_REFUSAL = "Separation of duties: you cannot review a record from a job you started."

# state -> states it may move to (SPEC §4). "finalised" is reached only through finalise().
TRANSITIONS: dict[str, frozenset[str]] = {
    "awaiting_review": frozenset({"approved", "rejected", "excluded"}),
    "approved": frozenset({"finalised", "awaiting_review", "auto_qa_failed", "rejected", "excluded"}),
    "auto_qa_failed": frozenset(),  # fixed by a rule/code change and a re-run, never approved
    "rejected": frozenset(),
    "excluded": frozenset(),
    "finalised": frozenset({"withdrawn"}),
}
_EVENTS = {"approved": "record.approved", "rejected": "record.rejected", "excluded": "record.excluded"}


def check_transition(current: str, new: str) -> None:
    if new not in TRANSITIONS.get(current, frozenset()):
        raise DeidError(f"a record in state {current} cannot become {new}")


def record_decision(
    ctx: ServiceContext,
    record_id: str,
    version: int,
    reviewer_id: str,
    decision: str,
    finding_category: str | None = None,
    reason: str | None = None,
) -> None:
    """``decision`` = approved (needs a finding category), rejected or excluded (need a reason code)."""
    if decision == "approved" and finding_category not in finding_codes():
        raise DeidError("a finding category from finding_categories.json is required to approve")
    if decision in ("rejected", "excluded") and not reason:
        raise DeidError("a reason code is required")
    with ctx.db.transaction() as s:
        ver = recs.get_version(s, record_id, version)
        record = recs.get_record(s, record_id)
        if ver is None or record is None:
            raise DeidError("no such record version")
        check_transition(ver.state, decision)
        ver.state, ver.reviewer_id, ver.decided_at = decision, reviewer_id, now_iso()
        ver.finding_category, ver.hold_reason = finding_category, None
        record.state = decision if record.latest_version == version else record.state
        details = {"version": version, "finding_category": finding_category, "reason": reason}
        audit.append_event(s, _EVENTS[decision], "record", record_id, details, reviewer_id)


def review_decision(
    ctx: ServiceContext,
    actor: Actor,
    record_id: str,
    version: int,
    decision: str,
    finding_category: str | None = None,
    reason: str | None = None,
) -> None:
    access.check(ctx, actor, "records.review", "records.review")
    with ctx.db.session() as s:
        ver = recs.get_version(s, record_id, version)
        if ver is None:
            raise not_found("No such record version.")
        own_job = cfg.job_starter(s, ver.job_id) == actor.user_id
        sod = sod_enabled(s)
    if sod and own_job:
        raise AppError(403, "separation_of_duties", SOD_REFUSAL)
    try:
        record_decision(ctx, record_id, version, actor.user_id, decision, finding_category, reason)
    except DeidError as exc:
        raise AppError(409, "review", str(exc)) from None
