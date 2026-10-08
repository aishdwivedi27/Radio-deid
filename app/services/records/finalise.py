"""Finalise one approved record version (SPEC §5.3). TR-REL-NF-01, TR-QA-02, TR-COH-03, TR-COH-04.

1. compose the final rows (reviewer, times, finding category, cohort ref), validate them, write
   ``record.json`` = the exact JSON line, and re-run C1-C8; a failure → ``auto_qa_failed``;
2. check the cohort cap; reached → stays ``awaiting_review`` with the cap message;
3. one DB transaction: version, new image rows, record, ledger → finalised, plus the ``record.finalised``
   audit event (D-020: in this transaction so a crash cannot lose it);
4. move the pending folder into ``output/records/<record_id>/`` (merging a new version);
5. append to records.jsonl, images.jsonl, records.csv, images.csv (``append.py``).
A crash after step 3 is completed by ``reconcile``. Finalising a finalised version again does nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.deid.qa.c_checks import check_consistency
from app.deid.record.canonical import json_line, write_record_json
from app.deid.record.finalrow import Decision, final_image_row, final_record_row
from app.deid.record.validate import validate_row
from app.deid.types import DeidError, PendingRecord
from app.services.context import ServiceContext, now_iso
from app.services.governance import ethics
from app.services.records.append import AppendReport, append_rows
from app.services.records.approve import check_transition
from app.store import audit
from app.store.output.folder import install_folder
from app.store.repos import governance as gov
from app.store.repos import ledger
from app.store.repos import records as recs

FLAGGED_MESSAGE = "Patient is on the OPT_OUT or STAFF_VIP list — exclude (ethics)."


@dataclass
class FinaliseResult:
    status: str  # finalised | already_finalised | auto_qa_failed | held
    record_id: str
    version: int
    record_row: dict[str, Any] | None = None
    image_rows: list[dict[str, Any]] = field(default_factory=list)  # only the rows appended now
    findings: list[str] = field(default_factory=list)
    message: str = ""
    append: AppendReport | None = None


@dataclass
class _Plan:
    pending_row: dict[str, Any]
    record_row: dict[str, Any]
    all_rows: list[dict[str, Any]]
    new_rows: list[dict[str, Any]]
    patient_code: str
    study_key: str
    c7: dict[str, str]
    hold: str | None


def _plan(ctx: ServiceContext, record_id: str, version: int, reviewer_id: str, category: str) -> _Plan | None:
    with ctx.db.session() as s:
        ver, record = recs.get_version(s, record_id, version), recs.get_record(s, record_id)
        if ver is None or record is None:
            raise DeidError("no such record version")
        if ver.finalised_at is not None:
            return None
        check_transition(ver.state, "finalised")
        fin = {i.image_id: json.loads(i.row_json) for i in recs.images(s, record_id, recs.FINALISED)}
        new = [json.loads(i.row_json) for i in recs.images(s, record_id, recs.PENDING)]
        ts = now_iso()
        decision = Decision(reviewer_id, ver.decided_at or ts, ts, category, ethics.cohort_ref(s))
        flagged = {"OPT_OUT", "STAFF_VIP"} & gov.flags_by_patient(s).get(record.patient_code, frozenset())
        hold = FLAGGED_MESSAGE if flagged else ethics.cap_hold(s, record.finalised_version is None, len(new))
        pending_row = json.loads(ver.row_json)
        new_rows = [final_image_row(r, ts) for r in new]
        by_id = fin | {r["image_id"]: r for r in new_rows}
        return _Plan(
            pending_row=pending_row,
            record_row=final_record_row(pending_row, decision),
            all_rows=[by_id[i] for i in pending_row["image_ids"] if i in by_id],
            new_rows=new_rows,
            patient_code=record.patient_code,
            study_key=record.study_key,
            c7=recs.c7_index(s),
            hold=hold,
        )


def _set_state(ctx: ServiceContext, record_id: str, version: int, state: str, **fields: Any) -> None:
    with ctx.db.transaction() as s:
        ver, record = recs.get_version(s, record_id, version), recs.get_record(s, record_id)
        assert ver is not None and record is not None
        ver.state = state
        for k, v in fields.items():
            setattr(ver, k, v)
        if record.latest_version == version:
            record.state = state


def _commit(ctx: ServiceContext, record_id: str, version: int, plan: _Plan, category: str) -> None:
    row = plan.record_row
    with ctx.db.transaction() as s:
        ver, record = recs.get_version(s, record_id, version), recs.get_record(s, record_id)
        assert ver is not None and record is not None
        ver.state, ver.row_json, ver.finalised_at = (
            "finalised",
            json_line(row).decode("utf-8"),
            row["finalised_at"],
        )
        ver.reviewer_id, ver.decided_at = row["qa"]["reviewer_id"], row["qa"]["decided_at"]
        ver.finding_category, ver.hold_reason = category, None
        new_by_id = {r["image_id"]: r for r in plan.new_rows}
        for img in recs.images(s, record_id, recs.PENDING):
            img.state, img.row_json = recs.FINALISED, json_line(new_by_id[img.image_id]).decode("utf-8")
        record.finalised_version, record.state = version, "finalised"
        record.latest_version = max(record.latest_version, version)
        ledger.finalise(s, record_id, version)
        details = {"version": version, "n_images": row["n_images"], "new_images": len(plan.new_rows)}
        audit.append_event(s, "record.finalised", "record", record_id, details, row["qa"]["reviewer_id"])


def finalise(
    ctx: ServiceContext, record_id: str, version: int, reviewer_id: str, finding_category: str
) -> FinaliseResult:
    plan = _plan(ctx, record_id, version, reviewer_id, finding_category)
    if plan is None:
        return FinaliseResult("already_finalised", record_id, version)
    validate_row("record", plan.record_row)
    for r in plan.new_rows:
        validate_row("image", r)
    folder = ctx.paths.pending_root / record_id
    rec = PendingRecord(
        "awaiting_review",
        record_id,
        plan.patient_code,
        plan.study_key,
        folder,
        plan.record_row,
        plan.all_rows,
    )
    write_record_json(folder, plan.record_row)
    findings = check_consistency(folder, rec, plan.c7)
    if findings or plan.hold:
        write_record_json(folder, plan.pending_row)  # the folder again matches the stored pending row
        if findings:
            notes = [str(f) for f in findings]
            _set_state(ctx, record_id, version, "auto_qa_failed", findings_json=json.dumps(notes))
            return FinaliseResult("auto_qa_failed", record_id, version, findings=notes)
        _set_state(ctx, record_id, version, "awaiting_review", hold_reason=plan.hold)
        return FinaliseResult("held", record_id, version, message=plan.hold or "")
    _commit(ctx, record_id, version, plan, finding_category)
    install_folder(folder, ctx.paths.records_dir / record_id, json_line(plan.record_row))
    report = append_rows(ctx, record_id)
    return FinaliseResult("finalised", record_id, version, plan.record_row, plan.new_rows, append=report)
