"""Ethics configuration, cohort cap and withdrawals through the store (SPEC §12.1, §13.1). TR-COH-01..04,
TR-WDR-01..03, TR-LIST-02. Fixtures T9 (consent after the cut-off), T10 (withdrawal), T16 (cohort cap).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path

import pytest

from app.deid import pseudonyms as ps
from app.deid.types import DeidError
from app.services.context import ServiceContext
from app.services.governance.ethics import CAP_MESSAGE, ApprovalInput, record_approval
from app.services.records import withdraw as withdraw_mod
from app.services.records.finalise import FLAGGED_MESSAGE
from app.services.records.query import list_records, release_candidates
from app.services.records.reconcile import reconcile
from app.services.records.withdraw import add_flags
from app.store.models.records import Exclusion
from app.store.repos import records as recs
from tests.deid.helpers import write_study
from tests.helpers.outputs import assert_outputs_consistent
from tests.services.conftest import approve_finalise, ingest_folder, lines


def _approval(cap: int = 1000, unit: str = "studies", ref: str = "EC-TEST-001") -> ApprovalInput:
    d = dt.date
    return ApprovalInput(
        committee_name="Synthetic Test EC",
        approval_ref=ref,
        protocol_version="1.0",
        approval_date=d(2026, 1, 1),
        expiry_date=d(2028, 1, 1),
        archive_start=d(2020, 1, 1),
        waiver_cutoff=d(2026, 6, 1),  # synthetic studies are dated 2026-09-01: after the cut-off
        cohort_cap=cap,
        cap_unit=unit,
        notice_start=d(2026, 1, 1),
        optout_window_days=60,
        legal_opinion=True,
    )


def _study(tmp_path: Path, name: str, n: int = 1, date: str = "20260901") -> Path:
    folder = tmp_path / "in" / name
    write_study(folder, n, modality="CT", patient_id=f"DEMO-G-{name}", StudyDate=date)
    return folder


def test_t9_consent_after_cutoff(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    record_approval(ctx, _approval(), "u_custodian")
    [no_consent] = ingest_folder(ctx, _study(tmp_path, "a"), key)
    assert no_consent.status == "excluded"
    with ctx.db.session() as s:
        [row] = s.query(Exclusion).all()
        assert row.reason == "NO_CONSENT_AFTER_CUTOFF" and row.patient_code.startswith("P")
    add_flags(ctx, "CONSENT", [(ps.patient_code(key, "DEMO-G-b"), "Yes", "2026-08-01")], "u_custodian")
    [consented] = ingest_folder(ctx, _study(tmp_path, "b"), key)
    assert consented.status == "awaiting_review"
    approve_finalise(ctx, consented.record_id, 1)
    [csv_row] = list(csv.DictReader(io.StringIO((ctx.paths.output_root / "records.csv").read_text("utf-8"))))
    assert csv_row["consent_basis"] == "consent_flag" and csv_row["cohort_ref"] == "EC-TEST-001"
    [early] = ingest_folder(ctx, _study(tmp_path, "c", date="20260301"), key)
    assert early.record is not None and early.record.record_row is not None
    assert early.record.record_row["consent_basis"] == "waiver"


def test_t16_cohort_cap_holds_records(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    record_approval(ctx, _approval(cap=1), "u_custodian")
    for name in ("a", "b"):
        add_flags(ctx, "CONSENT", [(ps.patient_code(key, f"DEMO-G-{name}"), "Yes", None)], "u_custodian")
    [a] = ingest_folder(ctx, _study(tmp_path, "a"), key)
    [b] = ingest_folder(ctx, _study(tmp_path, "b"), key)
    assert approve_finalise(ctx, a.record_id, 1).status == "finalised"
    held = approve_finalise(ctx, b.record_id, 1)
    assert (
        held.status == "held" and held.message == CAP_MESSAGE == "Cohort cap reached — EC amendment required."
    )
    with ctx.db.session() as s:
        ver = recs.get_version(s, b.record_id, 1)
        assert ver is not None and ver.state == "awaiting_review" and ver.hold_reason == CAP_MESSAGE
    assert len(lines(ctx.paths.output_root / "records.jsonl")) == 1
    assert not (ctx.paths.records_dir / b.record_id).exists()
    record_approval(ctx, _approval(cap=2, ref="EC-TEST-002"), "u_custodian")  # a new reference raises it
    assert approve_finalise(ctx, b.record_id, 1).status == "finalised"
    assert_outputs_consistent(ctx.paths.output_root, ctx)


def test_cap_in_images_counts_new_images(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    record_approval(ctx, _approval(cap=3, unit="images"), "u_custodian")
    add_flags(ctx, "CONSENT", [(ps.patient_code(key, "DEMO-G-a"), "Yes", None)], "u_custodian")
    [a] = ingest_folder(ctx, _study(tmp_path, "a", n=4), key)
    assert approve_finalise(ctx, a.record_id, 1).status == "held"


def test_approval_config_is_validated(ctx: ServiceContext) -> None:
    with pytest.raises(DeidError):
        record_approval(ctx, _approval().__class__(**{**_approval().__dict__, "optout_window_days": 30}), "u")


def test_t10_withdrawal(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    [a] = ingest_folder(ctx, _study(tmp_path, "a"), key)
    [b] = ingest_folder(ctx, _study(tmp_path, "b"), key)
    approve_finalise(ctx, a.record_id, 1)
    approve_finalise(ctx, b.record_id, 1)
    records_before = (ctx.paths.output_root / "records.jsonl").read_bytes()
    result = add_flags(ctx, "OPT_OUT", [(ps.patient_code(key, "DEMO-G-a"), None, None)], "u_custodian")
    assert result.withdrawn == [a.record_id]
    [w] = [json.loads(x) for x in lines(ctx.paths.output_root / "withdrawals.jsonl")]
    assert (w["record_id"], w["reason"], w["list_version"]) == (a.record_id, "OPT_OUT", result.list_version)
    assert (ctx.paths.output_root / "records.jsonl").read_bytes() == records_before  # never rewritten
    assert not (ctx.paths.records_dir / a.record_id).exists()
    assert (ctx.paths.withdrawn_dir / a.record_id / "record.json").exists()
    assert [r["record_id"] for r in list_records(ctx)] == [b.record_id]
    assert {r["record_id"] for r in list_records(ctx, _filters(include_withdrawn=True))} == {
        a.record_id,
        b.record_id,
    }
    assert release_candidates(ctx) == [(b.record_id, 1)]  # "the next release excludes them"
    again = add_flags(ctx, "STAFF_VIP", [(ps.patient_code(key, "DEMO-G-a"), None, None)], "u_custodian")
    assert again.withdrawn == [] and len(lines(ctx.paths.output_root / "withdrawals.jsonl")) == 1
    assert reconcile(ctx).changed is False
    assert assert_outputs_consistent(ctx.paths.output_root, ctx).withdrawal_lines == 1


def test_withdrawal_crash_before_folder_move(
    ctx: ServiceContext, tmp_path: Path, key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    [a] = ingest_folder(ctx, _study(tmp_path, "a"), key)
    approve_finalise(ctx, a.record_id, 1)

    def boom(*_a: object) -> str:
        raise RuntimeError("crash")

    monkeypatch.setattr(withdraw_mod, "move_folder", boom)
    with pytest.raises(RuntimeError):
        add_flags(ctx, "OPT_OUT", [(ps.patient_code(key, "DEMO-G-a"), None, None)], "u_custodian")
    monkeypatch.undo()
    report = reconcile(ctx)
    assert report.folders_withdrawn == 1 and report.appended == 2 and report.mismatches == []
    assert_outputs_consistent(ctx.paths.output_root, ctx)


def test_flagged_pending_record_is_not_finalised(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    [a] = ingest_folder(ctx, _study(tmp_path, "a"), key)
    add_flags(ctx, "STAFF_VIP", [(ps.patient_code(key, "DEMO-G-a"), None, None)], "u_custodian")
    held = approve_finalise(ctx, a.record_id, 1)
    assert held.status == "held" and held.message == FLAGGED_MESSAGE
    assert not (ctx.paths.output_root / "records.jsonl").exists()


def _filters(**kw: object):  # type: ignore[no-untyped-def]
    from app.services.records.query import RecordFilters

    return RecordFilters(**kw)  # type: ignore[arg-type]
