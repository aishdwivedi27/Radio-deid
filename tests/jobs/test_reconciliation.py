"""Skip list, reconciliation and report counts per job (SPEC §6.4, §6.5; TR-ELIG-10..15; T28, T29, T30,
T33, T35 job part). Folder names are planted as fake patient names to prove they stay under
``app_data/secure/``."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from app.services.jobs import secure_files
from tests.deid.helpers import write_study
from tests.jobs.conftest import Site, sse

PLANTED = "Sharma_Ramesh_016Y"  # a folder named after a (fake) patient, as real exports often are


def _start(site: Site, folder: Path) -> str:
    r = site.clients["operator"].post("/api/jobs/folder", json={"path": str(folder)})
    assert r.status_code == 202, r.text
    site.run()
    return str(r.json()["job_id"])


def _recon(site: Site, job_id: str) -> dict[str, dict[str, str]]:
    text = site.clients["reviewer"].get(f"/api/jobs/{job_id}/reconciliation.csv").text
    return {row["folder"]: row for row in csv.DictReader(io.StringIO(text))}


def _t28_input(root: Path) -> None:
    write_study(root / PLANTED, n=1, patient_id="DEMO-R-1", accession="ACC-R1", PatientAge="016Y")
    write_study(root / "noage", n=1, patient_id="DEMO-R-2", accession="ACC-R2", PatientAge=None)
    write_study(root / "us", n=1, patient_id="DEMO-R-3", accession="ACC-R3", modality="US",
                StudyDescription="USG OBSTETRIC")  # fmt: skip
    write_study(
        root / "head", n=2, patient_id="DEMO-R-4", accession="ACC-R4", modality="CT", BodyPartExamined="HEAD"
    )
    for i in (5, 6, 7):
        write_study(root / f"ok{i}", n=1, patient_id=f"DEMO-R-{i}", accession=f"ACC-R{i}")


def test_t28_skip_list_and_reconciliation(site: Site) -> None:
    _t28_input(site.inbox / "t28")
    job_id = _start(site, site.inbox / "t28")
    body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "finished" and body["balanced"]
    assert body["by_reason"] == {"AGE_UNDER_18": 1, "AGE_UNKNOWN": 1, "FACE_BEARING": 1, "OB_PELVIC_US": 1}
    assert body["counts"]["awaiting_review"] == 3
    rows = site.clients["custodian"].get(f"/api/jobs/{job_id}/exclusions").json()["rows"]
    assert len(rows) == 4
    by_reason = {r["reason"]: r for r in rows}
    assert by_reason["AGE_UNDER_18"]["source_path"] == PLANTED
    assert by_reason["AGE_UNDER_18"]["evidence"] == "age 016Y from PatientAge"
    assert by_reason["FACE_BEARING"]["file_count"] == "2" and by_reason["FACE_BEARING"]["modality"] == "CT"
    for r in rows:
        assert r["rule_id"].startswith("R") and r["rules_version"] and r["screened_at"]
        for planted in ("DEMO-R", "ACC-R", "Demo^Patient", "Demo Patient", PLANTED):
            assert planted not in r["evidence"]
    recon = _recon(site, job_id)
    assert recon["TOTAL"]["studies_found"] == "7" and recon["TOTAL"]["balanced"] == "yes"
    assert recon["TOTAL"]["AGE_UNDER_18"] == "1" and recon["TOTAL"]["skipped"] == "4"
    assert all(r["balanced"] == "yes" for r in recon.values())
    path = secure_files.exclusions_path(site.ctx, job_id)
    assert path.is_relative_to(site.ctx.paths.secure_dir)


def _scan_outside_secure(site: Site, needle: str) -> list[str]:
    hits = []
    for base in (site.ctx.paths.output_root, site.ctx.paths.app_data_dir, site.data / "logs"):
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if (
                p.is_file()
                and not p.is_relative_to(site.ctx.paths.secure_dir)
                and needle.encode() in p.read_bytes()
            ):
                hits.append(p.name)
    return hits


def test_t29_spot_check_and_no_path_outside_secure(site: Site, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    _t28_input(site.inbox / "t29")
    job_id = _start(site, site.inbox / "t29")
    cus = site.clients["custodian"]
    listing = cus.get(f"/api/jobs/{job_id}/exclusions").json()
    for row in listing["rows"]:
        folder = Path(row["open_path"][0])
        files = [folder / n for n in row["file_names"].split(";")]
        shas = [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
        assert ";".join(shas) == row["file_sha256_list"]  # each listed path opens the right study
        r = cus.post(f"/api/jobs/{job_id}/exclusions/{row['study_key']}/verify",
                     json={"result": "confirmed", "note": f"checked {PLANTED}"})  # fmt: skip
        assert r.status_code == 200, r.text
    assert site.clients["operator"].get(f"/api/jobs/{job_id}/exclusions").status_code == 403
    audit = cus.get("/api/audit", params={"limit": 500}).json()
    verified = [a for a in audit if a["action"] == "exclusion.verified"]
    assert len(verified) == 4
    assert PLANTED not in json.dumps(audit) and str(site.inbox) not in json.dumps(verified)
    assert _scan_outside_secure(site, PLANTED) == []
    assert PLANTED not in caplog.text
    op = site.clients["operator"]
    bodies = [op.get(f"/api/jobs/{job_id}").text, op.get("/api/jobs").text, op.get("/api/pending").text,
              json.dumps(sse(op, job_id))]  # fmt: skip
    assert all(PLANTED not in b for b in bodies)
    assert PLANTED in json.dumps(op.get(f"/api/jobs/{job_id}/folders").json())  # the one place it is shown
    assert (secure_files.exclusions_dir(site.ctx, job_id) / "spotcheck.csv").is_file()


def test_t30_per_folder_breakdown(site: Site, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    root = site.inbox / "t30"
    write_study(
        root / "A" / "s1",
        n=2,
        patient_id="DEMO-T-1",
        accession="ACC-T1",
        modality="CT",
        BodyPartExamined="CHEST",
    )
    write_study(
        root / "A" / "s2",
        n=1,
        patient_id="DEMO-T-2",
        accession="ACC-T2",
        modality="CT",
        BodyPartExamined="CHEST",
    )
    write_study(root / "A" / "s3", n=1, patient_id="DEMO-T-3", accession="ACC-T3", PatientAge="015Y")
    write_study(
        root / "B" / "us",
        n=1,
        patient_id="DEMO-T-4",
        accession="ACC-T4",
        modality="US",
        BodyPartExamined="ABDOMEN",
    )
    (root / "B" / "us" / "ACC-T4.txt").write_text("USG: LMP 12/08/2026. Liver normal.\n", encoding="utf-8")
    (root / "B" / "pdf_only").mkdir(parents=True)
    (root / "B" / "pdf_only" / "scan.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (root / "C").mkdir()
    write_study(root, n=1, patient_id="DEMO-T-5", accession="ACC-T5", prefix="ROOT")
    job_id = _start(site, root)
    recon = _recon(site, job_id)
    assert set(recon) == {"A", "B", "C", "(root)", "TOTAL"}
    a, b, c, r0, total = recon["A"], recon["B"], recon["C"], recon["(root)"], recon["TOTAL"]
    assert (a["studies_found"], a["processed"], a["skipped"], a["AGE_UNDER_18"]) == ("3", "2", "1", "1")
    assert (b["studies_found"], b["skipped"], b["OB_PELVIC_US"], b["images_excluded"]) == ("1", "1", "1", "1")
    assert all(c[k] == "0" for k in ("studies_found", "files_found", "processed", "skipped", "errors"))
    assert (r0["studies_found"], r0["processed"]) == ("1", "1")
    assert total["studies_found"] == "5" and all(row["balanced"] == "yes" for row in recon.values())
    for k in ("studies_found", "processed", "skipped", "files_found"):
        assert int(total[k]) == sum(int(recon[f][k]) for f in ("A", "B", "C", "(root)"))
    audit = json.dumps(site.clients["custodian"].get("/api/audit", params={"limit": 500}).json())
    assert "pdf_only" not in audit and "pdf_only" not in caplog.text


def test_t33_report_counts(site: Site) -> None:
    root = site.inbox / "t33"
    write_study(root / "s1", n=1, patient_id="DEMO-Q-1", accession="ACC-Q1", name="Rao^Kiran")
    (root / "s1" / "ACC-Q1_report.txt").write_text("Normal study.\n", encoding="utf-8")
    write_study(root / "s2", n=1, patient_id="DEMO-Q-2", accession="ACC-Q2", name="Iyer^Mala")
    (root / "s2" / "ACC-Q2.txt").write_text("No abnormality.\n", encoding="utf-8")
    write_study(root / "s3", n=1, patient_id="DEMO-Q-3", accession="ACC-Q3")
    write_study(root / "s4", n=1, patient_id="DEMO-Q-4", accession="ACC-Q4")
    (root / "reports").mkdir()
    (root / "reports" / "Ramesh_Sharma.txt").write_text("Name: Ramesh Sharma 58Y\n", encoding="utf-8")
    job_id = _start(site, root)
    body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
    assert (body["with_report"], body["without_report"]) == (2, 2)
    assert (body["reports_found"], body["reports_unmatched"]) == (3, 1)
    assert body["balanced"] and all(f["balanced"] for f in body["folders"])
    recon = _recon(site, job_id)
    assert (recon["TOTAL"]["with_report"], recon["TOTAL"]["without_report"]) == ("2", "2")
    indexed = next(d for k, d, _ in sse(site.clients["operator"], job_id) if k == "indexed")
    assert (indexed["reports_found"], indexed["reports_unmatched"]) == (3, 1)


def test_t35_batches_job_part(site: Site) -> None:
    from pydicom.uid import generate_uid

    from tests.services.conftest import approve_finalise

    uid = generate_uid()
    b1, b2 = site.inbox / "batch1", site.inbox / "batch2"
    write_study(b1 / "X", n=2, patient_id="DEMO-B-1", accession="ACC-B1", study_uid=uid)
    write_study(b1 / "Y", n=1, patient_id="DEMO-B-2", accession="ACC-B2")
    job1 = _start(site, b1)
    pending = {p["record_id"]: p for p in site.clients["reviewer"].get("/api/pending").json()}
    with site.ctx.db.session() as s:
        from app.store.models.jobs import JobStudy

        x_rid = s.query(JobStudy).filter_by(job_id=job1, n_files=2).one().record_id
    approve_finalise(site.ctx, x_rid, 1)
    lines = (site.ctx.paths.output_root / "records.jsonl").read_bytes()
    rerun = _start(site, b1)  # batch 1 again: nothing new
    body = site.clients["operator"].get(f"/api/jobs/{rerun}").json()
    assert body["counts"]["finalised"] == 1 and body["counts"]["awaiting_review"] == 1 and body["balanced"]
    assert (site.ctx.paths.output_root / "records.jsonl").read_bytes() == lines
    assert len(site.clients["reviewer"].get("/api/pending").json()) == len(pending) - 1
    write_study(b2 / "X", n=3, patient_id="DEMO-B-1", accession="ACC-B1", study_uid=uid, prefix="IM")
    for f in sorted((b2 / "X").iterdir())[:2]:
        f.unlink()  # batch 2 carries only the third image of study X
    _start(site, b2)
    d = site.clients["reviewer"].get(f"/api/pending/{x_rid}").json()
    assert d["version"] == 2 and len(d["append_preview"]["images"]["json"]) == 1
    assert d["append_preview"]["record"]["json"]["n_images"] == 3
