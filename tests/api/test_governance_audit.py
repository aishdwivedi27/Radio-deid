"""Pre-approval exit, patient lists (T6), review separation of duties (criterion 3), pre-approval refusal
(T21) and the audit log (view, filters, CSV export, chain verification, tamper). TR-COH-05, TR-LIST-01..05,
TR-ROLE-04, TR-SEC-03."""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.errors import Actor, AppError
from app.deid import pseudonyms as ps
from app.deid.keys import load_or_create_key
from app.services.auth import settings as tech
from app.services.governance.ethics import assert_not_preapproval
from app.services.records.approve import review_decision
from app.store import audit
from app.store.repos import settings as cfg
from tests.api.conftest import BAD_GUESS, GOOD, Station
from tests.deid.helpers import write_study
from tests.services.conftest import FAST, ingest_folder

APPROVAL = {
    "committee_name": "Demo EC", "approval_ref": "EC-DEMO-001", "protocol_version": "1.0",
    "approval_date": "2026-09-01", "expiry_date": "2027-09-01", "archive_start": "2020-01-01",
    "waiver_cutoff": "2026-12-31", "cohort_cap": 1000, "cap_unit": "studies", "notice_start": "2026-09-01",
    "optout_window_days": 60, "legal_opinion": True, "legal_opinion_date": "2026-09-02",
}  # fmt: skip
Staffed = tuple[Station, dict[str, TestClient]]


def _key(st: Station) -> bytes:
    return load_or_create_key(st.ctx.paths.key_path)


def test_leaving_preapproval_needs_custodian_and_password(staffed: Staffed) -> None:
    station, clients = staffed
    with station.ctx.db.session() as s, pytest.raises(AppError) as err:
        assert_not_preapproval(s)
    assert err.value.status == 409
    cust = clients["custodian"]
    assert cust.post("/api/ethics/approval", json={**APPROVAL, "password": BAD_GUESS}).status_code == 403
    bad = {**APPROVAL, "optout_window_days": 30, "password": GOOD}
    assert cust.post("/api/ethics/approval", json=bad).status_code == 400
    assert clients["admin"].get("/api/status").json()["preapproval"] is True
    r = cust.post("/api/ethics/approval", json={**APPROVAL, "password": GOOD})
    assert r.status_code == 200 and r.json()["preapproval"] is False
    assert clients["operator"].get("/api/status").json() == {
        "setup_required": False, "setup_step": "done", "preapproval": False, "banner": None,
    }  # fmt: skip
    with station.ctx.db.session() as s:
        assert_not_preapproval(s)
        assert [
            e.user_id for e in audit.iter_events(s, audit.AuditFilter(action="ethics.config_changed"))
        ] == [station.ids["custodian1"]]


def test_t6_staff_vip_import_excludes_and_stores_no_raw_id(staffed: Staffed, tmp_path: Path) -> None:
    station, clients = staffed
    raw = "DEMO-T6-VIP-0001"
    body = f"uhid,name\n{raw},ignored column\nDEMO-T6-OTHER-9,x\n".encode()
    r = clients["custodian"].post("/api/lists/staff_vip", content=body, headers={"Content-Type": "text/csv"})
    assert r.status_code == 200 and r.json()["withdrawn"] == 0
    bad = b"uhid\n\n"
    r = clients["custodian"].post(
        "/api/lists/OPT_OUT", content=b"uhid\n ,\n", headers={"Content-Type": "text/csv"}
    )
    assert r.status_code == 400 and "row 2" in r.text
    assert clients["custodian"].post("/api/lists/OPT_OUT", content=bad,
                                     headers={"Content-Type": "text/csv"}).status_code == 400  # fmt: skip
    write_study(tmp_path / "in" / "s1", 1, patient_id=raw)
    [res] = ingest_folder(station.ctx, tmp_path / "in" / "s1", _key(station), FAST)
    assert res.status == "excluded" and res.record and res.record.screen.reason == "STAFF_VIP"  # type: ignore[union-attr]
    station.ctx.db.engine.dispose()
    db = station.ctx.paths.app_data_dir
    blob = b"".join(p.read_bytes() for p in db.glob("app.db*"))
    assert raw.encode() not in blob and b"ignored column" not in blob
    assert ps.patient_code(_key(station), raw).encode() in blob


def test_criterion_3_operator_cannot_approve_and_no_own_job(staffed: Staffed, tmp_path: Path) -> None:
    station, clients = staffed
    ctx, ids = station.ctx, station.ids
    with ctx.db.transaction() as s:
        cfg.add_job(s, FAST.job_id, ids["admin1"], "finished", "2026-10-08T10:00:00+05:30")
    write_study(tmp_path / "in" / "s2", 1, patient_id="DEMO-C3-0001")
    [res] = ingest_folder(ctx, tmp_path / "in" / "s2", _key(station), FAST)
    rid = res.record_id
    operator = Actor(ids["operator1"], frozenset({"operator"}))
    admin = Actor(ids["admin1"], frozenset({"admin"}))
    reviewer = Actor(ids["reviewer1"], frozenset({"reviewer"}))
    with pytest.raises(AppError) as err:
        review_decision(ctx, operator, rid, 1, "approved", "NORMAL")
    assert err.value.code == "forbidden"
    with pytest.raises(AppError) as err:
        review_decision(ctx, admin, rid, 1, "approved", "NORMAL")  # the admin started this job
    assert err.value.code == "separation_of_duties"
    tech.set_sod(ctx, admin, False)
    with ctx.db.session() as s:
        assert tech.sod_enabled(s) is False
    tech.set_sod(ctx, admin, True)
    review_decision(ctx, reviewer, rid, 1, "approved", "NORMAL")
    with ctx.db.session() as s:
        [ev] = audit.iter_events(s, audit.AuditFilter(action="record.approved"))
        assert ev.user_id == ids["reviewer1"] and ev.target_id == rid
        assert [e.details_json for e in audit.iter_events(s, audit.AuditFilter(action="sod.toggled"))] == [
            '{"from":true,"to":false}',
            '{"from":false,"to":true}',
        ]


def test_sod_off_lets_the_job_starter_review(staffed: Staffed, tmp_path: Path) -> None:
    station, _ = staffed
    ctx, admin = station.ctx, Actor(station.ids["admin1"], frozenset({"admin"}))
    with ctx.db.transaction() as s:
        cfg.add_job(s, FAST.job_id, admin.user_id, "finished", "2026-10-08T10:00:00+05:30")
    write_study(tmp_path / "in" / "s3", 1, patient_id="DEMO-C3-0002")
    [res] = ingest_folder(ctx, tmp_path / "in" / "s3", _key(station), FAST)
    tech.set_sod(ctx, admin, False)
    review_decision(ctx, admin, res.record_id, 1, "approved", "NORMAL")


def test_t21_real_file_refused_in_preapproval_mode(station: Station, tmp_path: Path) -> None:
    write_study(tmp_path / "in" / "real", 2, patient_id="RX-REAL-77", InstitutionName="Real Hospital")
    [res] = ingest_folder(station.ctx, tmp_path / "in" / "real", _key(station), FAST)
    assert res.status == "excluded" and res.record.screen.reason == "PRE_APPROVAL_REAL_DATA"  # type: ignore[union-attr]
    paths = station.ctx.paths
    assert not any(paths.records_dir.glob("*")) if paths.records_dir.exists() else True
    assert not any(paths.pending_root.glob("S*")) if paths.pending_root.exists() else True
    with station.ctx.db.session() as s:
        [ev] = audit.iter_events(s, audit.AuditFilter(action="preapproval.refused_file"))
    assert (
        '"files":2' in ev.details_json
        and "RX-REAL" not in ev.details_json
        and "IM0001" not in ev.details_json
    )


def test_audit_view_filters_export_and_verify(staffed: Staffed) -> None:
    station, clients = staffed
    auditor = clients["auditor"]
    rows = auditor.get("/api/audit", params={"action": "user.created", "limit": 2}).json()
    assert len(rows) == 2 and all(r["action"] == "user.created" for r in rows)
    mine = auditor.get("/api/audit", params={"user_id": station.ids["admin1"]}).json()
    assert mine and {r["user_id"] for r in mine} == {station.ids["admin1"]}
    assert auditor.get("/api/audit", params={"from": "2999-01-01"}).json() == []
    r = auditor.get("/api/audit/export.csv", params={"action": "auth.login_success"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    table = list(csv.DictReader(io.StringIO(r.text)))
    assert table and {t["action"] for t in table} == {"auth.login_success"}
    assert {t["username"] for t in table} >= {"admin1", "auditor1"}
    assert auditor.get("/api/audit/verify").json()["ok"] is True
    last = auditor.get("/api/audit", params={"limit": 1}).json()[0]
    assert last["action"] in {"export.created", "access.denied", "auth.login_success"}
    assert clients["custodian"].get("/api/audit/verify").json()["ok"] is True


def test_tampering_breaks_the_chain_at_that_row(staffed: Staffed) -> None:
    station, clients = staffed
    db = station.ctx.paths.app_data_dir / "app.db"
    with station.ctx.db.session() as s:
        ids = [e.id for e in audit.iter_events(s, audit.AuditFilter())]
    target = ids[len(ids) // 2]
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE audit_events SET details_json='{}' WHERE id=?", (target,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM audit_events WHERE id=?", (target,))
    con.execute("DROP TRIGGER audit_events_no_update")  # someone with file access goes around it
    con.execute("UPDATE audit_events SET details_json='{\"edited\":1}' WHERE id=?", (target,))
    con.commit()
    con.close()
    r = clients["auditor"].get("/api/audit/verify").json()
    assert r == {"ok": False, "checked": ids.index(target), "first_bad_id": target}
