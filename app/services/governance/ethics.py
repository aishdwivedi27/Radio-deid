"""Ethics configuration and the cohort cap (SPEC §12.1). TR-COH-01..04.

Phase 2 provides the store side: record an approval, build the eligibility ``ScreenContext`` from the DB and
check the cap at finalisation. The Custodian-only, TOTP-confirmed screen arrives with auth (Phase 3+).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.deid.record.finalrow import PRE_APPROVAL_COHORT
from app.deid.types import DeidError, ScreenContext
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.repos import governance as gov
from app.store.repos import records as recs

CAP_MESSAGE = "Cohort cap reached — EC amendment required."


@dataclass(frozen=True)
class ApprovalInput:
    committee_name: str
    approval_ref: str
    protocol_version: str
    approval_date: dt.date
    expiry_date: dt.date
    archive_start: dt.date
    waiver_cutoff: dt.date
    cohort_cap: int
    cap_unit: str  # images | studies
    notice_start: dt.date
    optout_window_days: int
    legal_opinion: bool = False
    legal_opinion_date: dt.date | None = None
    ec_amendment_refs: tuple[str, ...] = ()


def record_approval(ctx: ServiceContext, a: ApprovalInput, user_id: str) -> int:
    if not 60 <= a.optout_window_days <= 90:
        raise DeidError("opt-out window must be 60-90 days")
    if a.cap_unit not in ("images", "studies") or a.cohort_cap < 0:
        raise DeidError("cohort cap needs a unit (images or studies) and a value >= 0")
    fields = {
        k: (v.isoformat() if isinstance(v, dt.date) else v)
        for k, v in a.__dict__.items()
        if k != "ec_amendment_refs"
    }
    fields |= {"ec_amendment_refs_json": json.dumps(list(a.ec_amendment_refs)), "created_by": user_id}
    with ctx.db.transaction() as s:
        approval = gov.add_approval(s, fields, now_iso())
        audit.append_event(
            s, "ethics.config_changed", "ethics_approval", str(approval.id), {"cap": a.cohort_cap}, user_id
        )
        return approval.id


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def screen_context(s: Session) -> ScreenContext:
    """The eligibility screen's view of the DB: approval state, dates and patient flags (codes only)."""
    approval = gov.active_approval(s)
    flags = gov.flags_by_patient(s)
    if approval is None:
        return ScreenContext(approval_active=False, flags=flags)
    return ScreenContext(
        approval_active=True,
        archive_start=_date(approval.archive_start),
        waiver_cutoff=_date(approval.waiver_cutoff),
        flags=flags,
    )


def cohort_ref(s: Session) -> str:
    approval = gov.active_approval(s)
    return approval.approval_ref if approval else PRE_APPROVAL_COHORT


def cap_hold(s: Session, new_study: bool, new_images: int) -> str | None:
    """The hold message if finalising would exceed the cap in its unit (TR-COH-04), else None. With no
    active approval (pre-approval mode, synthetic data only) there is no cap."""
    approval = gov.active_approval(s)
    if approval is None:
        return None
    studies, images = recs.usage(s)
    adding, used = (int(new_study), studies) if approval.cap_unit == "studies" else (new_images, images)
    return CAP_MESSAGE if adding and used + adding > approval.cohort_cap else None
