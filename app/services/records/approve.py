"""Review decisions on a pending version (SPEC §4, §6.3). TR-REV-03, TR-REV-04.

Phase 2 keeps the state machine and the stored decision. Phase 5 adds the role and separation-of-duties
checks, the mandatory checkboxes, the approval note and the API around it.
"""

from __future__ import annotations

from app.deid.record.finalrow import finding_codes
from app.deid.types import DeidError
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.repos import records as recs

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
