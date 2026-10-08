"""Ethics configuration and the cohort cap (SPEC §12.1). TR-COH-01..04.

Record an approval (Custodian only, confirmed with TOTP: ``configure_approval``), build the eligibility
``ScreenContext`` from the DB and check the cap at finalisation. Until an approval is active the app is in
pre-approval mode (SPEC §4.1, TR-COH-05): releases, re-identification samples and catalogue exports call
``assert_not_preapproval`` and are refused; no role can override it.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.errors import Actor, AppError, bad_request
from app.deid.record.finalrow import PRE_APPROVAL_COHORT
from app.deid.types import DeidError, ScreenContext
from app.services.auth import access
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.repos import governance as gov
from app.store.repos import records as recs

CAP_MESSAGE = "Cohort cap reached — EC amendment required."
PREAPPROVAL_BANNER = "Pre-approval mode — synthetic data only."
PREAPPROVAL_REFUSAL = "Releases and exports are disabled until the ethics approval is recorded."


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


def configure_approval(ctx: ServiceContext, actor: Actor, a: ApprovalInput, totp_code: str | None) -> int:
    """The Custodian records an approval with a fresh TOTP code; this also ends pre-approval mode."""
    access.check(ctx, actor, "ethics.configure", "ethics.approval")
    access.verify_step_up(ctx, actor, totp_code, "ethics.configure", "ethics.approval")
    try:
        return record_approval(ctx, a, actor.user_id)
    except DeidError as exc:
        raise bad_request(str(exc), "ethics") from None


def is_preapproval(ctx: ServiceContext) -> bool:
    with ctx.db.session() as s:
        return gov.active_approval(s) is None


def assert_not_preapproval(s: Session) -> None:
    """Called by every release, re-identification sample and catalogue export (TR-COH-05)."""
    if gov.active_approval(s) is None:
        raise AppError(409, "preapproval", PREAPPROVAL_REFUSAL)
