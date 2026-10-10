"""Pending records for review, read-only (SPEC §4, §6.3; TR-REV-01, TR-REV-06), and pre-approval refusal at
ingest through the upload API (SPEC §4.1; T21)."""

from __future__ import annotations

import json
from pathlib import Path

from app.deid.record.columns import IMAGE_COLUMNS, RECORD_COLUMNS
from app.deid.record.flatten import image_csv, record_csv
from app.store.output.csv_writer import encode_row
from app.store.repos import records as recs
from tests.deid.helpers import write_study
from tests.jobs.conftest import Site, folder_files, upload

PNG = b"\x89PNG\r\n\x1a\n"


def _job(site: Site, folder: Path) -> str:
    r = upload(site.clients["operator"], folder_files(folder))
    assert r.status_code == 202, r.text
    site.run()
    return str(r.json()["job_id"])


def _line(values: list[str]) -> str:
    return encode_row(values).decode("utf-8")


def test_pending_detail_shows_exact_rows(site: Site, tmp_path: Path) -> None:
    src = tmp_path / "src"
    write_study(src / "A", n=2, patient_id="DEMO-P-1", accession="ACC-P1", name="Rao^Kiran")  # not "Patient"
    (src / "A" / "ACC-P1_report.txt").write_text("FINDINGS: normal chest.\n", encoding="utf-8")
    write_study(src / "B", n=1, patient_id="DEMO-P-2", accession="ACC-P2")
    _job(site, src)
    rev = site.clients["reviewer"]
    queue = rev.get("/api/pending").json()
    assert len(queue) == 2 and {q["report_present"] for q in queue} == {True, False}
    with_report = next(q for q in queue if q["report_present"])
    no_report = next(q for q in queue if not q["report_present"])
    d = rev.get(f"/api/pending/{with_report['record_id']}").json()
    with site.ctx.db.session() as s:
        ver = recs.get_version(s, d["record_id"], d["version"])
        assert ver is not None
        row = json.loads(ver.row_json)
        image_rows = [json.loads(i.row_json) for i in recs.images(s, d["record_id"], recs.PENDING)]
    ap = d["append_preview"]
    assert ap["record"]["json"] == row
    assert ap["record"]["csv_header"] == _line(list(RECORD_COLUMNS))
    assert ap["record"]["csv_row"] == _line(record_csv(row))
    assert ap["images"]["json"] == image_rows and len(image_rows) == 2
    assert ap["images"]["csv_rows"] == [_line(image_csv(r)) for r in image_rows]
    assert ap["images"]["csv_header"] == _line(list(IMAGE_COLUMNS))
    assert "reviewer_id" in ap["filled_at_approval"] and "finalised_at" in ap["filled_at_approval"]
    assert d["report"]["text"].startswith("DE-IDENTIFIED REPORT") and d["banners"] == []
    assert rev.get(d["preview_url"]).content.startswith(PNG)
    for img in d["images"]:
        r = rev.get(img["url"])
        assert r.status_code == 200 and r.content.startswith(PNG)
    other = rev.get(f"/api/pending/{no_report['record_id']}").json()
    assert other["banners"] == ["NO_REPORT"] and other["report"]["text"] is None
    foreign = other["images"][0]["image_id"]
    assert rev.get(f"/api/pending/{d['record_id']}/image/{foreign}.png").status_code == 404
    assert rev.get(f"/api/pending/{d['record_id']}/image/{d['record_id']}-0099-000001.png").status_code == 404
    assert site.clients["auditor"].get("/api/pending").status_code == 403
    assert len(rev.get("/api/pending", params={"state": "auto_qa_failed"}).json()) == 0


def test_t21_real_data_refused_at_ingest(site: Site, tmp_path: Path) -> None:
    src = tmp_path / "real"
    write_study(src / "X", n=1, patient_id="UHID-123456", accession="ACC-R1", InstitutionName="City Hospital")
    job_id = _job(site, src)
    body = site.clients["operator"].get(f"/api/jobs/{job_id}").json()
    assert body["notice"] == "No real patient data may be processed before the ethics approval is recorded."
    assert body["by_reason"] == {"PRE_APPROVAL_REAL_DATA": 1} and body["counts"]["excluded"] == 1
    refused = (
        site.clients["custodian"].get("/api/audit", params={"action": "preapproval.refused_file"}).json()
    )
    assert len(refused) == 1 and "UHID" not in json.dumps(refused)
    assert site.clients["operator"].get("/api/pending").json() == []
    pending, out = site.ctx.paths.pending_root, site.ctx.paths.records_dir
    assert not (pending.exists() and any(pending.iterdir())) and not out.exists()
    assert not (site.ctx.paths.staging_dir / job_id).exists()
