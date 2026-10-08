"""Ethics approval, patient lists, flags and withdrawals (SPEC §12, §13.1). TR-COH-01, TR-LIST-02, TR-WDR-02.

Patient lists hold ``patient_code`` only (CLAUDE.md rule 13).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.store.models.governance import EthicsApproval, ListVersion, PatientFlag, Withdrawal
from app.store.models.records import Record

WITHDRAW_FLAGS = ("OPT_OUT", "STAFF_VIP")


def active_approval(s: Session) -> EthicsApproval | None:
    return s.scalars(select(EthicsApproval).where(EthicsApproval.active.is_(True))).first()


def add_approval(s: Session, fields: dict[str, Any], now: str) -> EthicsApproval:
    """Record a new approval and make it the only active one (history is kept)."""
    s.execute(update(EthicsApproval).where(EthicsApproval.active.is_(True)).values(active=False))
    s.flush()
    approval = EthicsApproval(**fields, active=True, created_at=now)
    s.add(approval)
    s.flush()
    return approval


def add_list_version(s: Session, list_type: str, rows: int, matched: int, importer_id: str, now: str) -> int:
    lv = ListVersion(
        list_type=list_type, rows=rows, matched=matched, importer_id=importer_id, imported_at=now
    )
    s.add(lv)
    s.flush()
    return lv.id


def add_flags(
    s: Session,
    list_version_id: int,
    flag_type: str,
    entries: Iterable[tuple[str, str | None, str | None]],
    now: str,
) -> int:
    """``entries`` = (patient_code, consent_value, consent_date). Returns the number of rows added."""
    n = 0
    for code, value, date in entries:
        s.add(
            PatientFlag(
                patient_code=code,
                flag_type=flag_type,
                list_version_id=list_version_id,
                consent_value=value,
                consent_date=date,
                added_at=now,
            )
        )
        n += 1
    s.flush()
    return n


def flags_by_patient(s: Session) -> dict[str, frozenset[str]]:
    """Flags per patient_code as the eligibility screen reads them. A CONSENT flag counts only when its
    value is Yes; OPT_OUT overrides CONSENT in the screen itself (TR-LIST-05)."""
    out: dict[str, set[str]] = defaultdict(set)
    for code, flag, value in s.execute(
        select(PatientFlag.patient_code, PatientFlag.flag_type, PatientFlag.consent_value)
    ):
        if flag != "CONSENT" or (value or "").lower() == "yes":
            out[code].add(flag)
    return {k: frozenset(v) for k, v in out.items()}


def records_to_withdraw(s: Session) -> Sequence[tuple[str, str, str, int]]:
    """(record_id, patient_code, flag_type, list_version_id) for finalised, not yet withdrawn records whose
    patient has an OPT_OUT or STAFF_VIP flag (earliest flag wins)."""
    q = (
        select(Record.record_id, Record.patient_code, PatientFlag.flag_type, PatientFlag.list_version_id)
        .join(PatientFlag, PatientFlag.patient_code == Record.patient_code)
        .where(
            Record.finalised_version.is_not(None),
            Record.state != "withdrawn",
            PatientFlag.flag_type.in_(WITHDRAW_FLAGS),
        )
        .order_by(Record.record_id, PatientFlag.id)
    )
    seen: dict[str, tuple[str, str, str, int]] = {}
    for rid, code, flag, lv in s.execute(q).all():
        seen.setdefault(rid, (rid, code, flag, lv))
    return list(seen.values())


def withdrawals(s: Session) -> Sequence[Withdrawal]:
    return s.scalars(select(Withdrawal).order_by(Withdrawal.id)).all()
