"""Patient flags and withdrawals (SPEC §4, §12.2, §13.1). TR-WDR-01..03, TR-LIST-02, TR-LIST-05.

``add_flags`` stores one list version and its flags (``patient_code`` only; the CSV import that hashes
UHIDs arrives with the Custodian screens). Any finalised record whose patient now has an OPT_OUT or
STAFF_VIP flag is withdrawn: DB first (state, withdrawals row, audit), then its folder moves from
``output/records/`` to ``app_data/withdrawn/``, then withdrawals.jsonl/.csv are appended.
records.jsonl is never rewritten. ``reconcile`` completes any step a crash interrupted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.deid.record.canonical import json_line
from app.deid.record.validate import validate_row
from app.deid.types import DeidError
from app.services.context import ServiceContext, now_iso
from app.services.records.append import WITHDRAWAL_FILES, AppendReport, append_rows
from app.store import audit
from app.store.models.governance import Withdrawal
from app.store.models.records import Record
from app.store.output.folder import move_folder
from app.store.repos import governance as gov

LIST_TYPES = ("OPT_OUT", "STAFF_VIP", "MEDICO_LEGAL", "CONSENT")


@dataclass
class FlagResult:
    list_version: int
    withdrawn: list[str] = field(default_factory=list)
    append: AppendReport | None = None


def add_flags(
    ctx: ServiceContext,
    list_type: str,
    entries: Sequence[tuple[str, str | None, str | None]],
    importer_id: str,
) -> FlagResult:
    """``entries`` = (patient_code, consent Yes/No or None, consent date or None)."""
    if list_type not in LIST_TYPES:
        raise DeidError("unknown list type")
    with ctx.db.transaction() as s:
        codes = {e[0] for e in entries}
        matched = sum(1 for c in codes if s.query(Record).filter(Record.patient_code == c).first())
        lv = gov.add_list_version(s, list_type, len(entries), matched, importer_id, now_iso())
        gov.add_flags(s, lv, list_type, entries, now_iso())
        audit.append_event(
            s,
            "list.imported",
            "list_version",
            str(lv),
            {"type": list_type, "rows": len(entries)},
            importer_id,
        )
    withdrawn, report = apply_withdrawals(ctx)
    return FlagResult(lv, withdrawn, report)


def apply_withdrawals(ctx: ServiceContext) -> tuple[list[str], AppendReport]:
    done: list[str] = []
    with ctx.db.transaction() as s:
        for rid, code, flag, lv in gov.records_to_withdraw(s):
            row = {
                "record_id": rid,
                "patient_code": code,
                "reason": flag,
                "list_version": lv,
                "withdrawn_at": now_iso(),
            }
            validate_row("withdrawal", row)
            record = s.get(Record, rid)
            assert record is not None
            record.state = "withdrawn"
            s.add(
                Withdrawal(
                    record_id=rid,
                    patient_code=code,
                    reason=flag,
                    list_version_id=lv,
                    withdrawn_at=row["withdrawn_at"],
                    row_json=json_line(row).decode("utf-8"),
                )
            )
            audit.append_event(s, "record.withdrawn", "record", rid, {"reason": flag, "list_version": lv})
            done.append(rid)
    move_withdrawn_folders(ctx)
    return done, append_rows(ctx, files=WITHDRAWAL_FILES)


def move_withdrawn_folders(ctx: ServiceContext) -> int:
    moved = 0
    with ctx.db.session() as s:
        ids = [w.record_id for w in gov.withdrawals(s)]
    for rid in ids:
        if move_folder(ctx.paths.records_dir / rid, ctx.paths.withdrawn_dir / rid) == "moved":
            moved += 1
    return moved
