"""Finalising the 4 sample records (acceptance criterion 2; SPEC §5.3). TR-REL-NF-01, TR-QA-02, TR-DEID-10.

Exactly 4 lines in records.jsonl/.csv and 23 in images.jsonl/.csv, and every ID matches across the folder,
the DICOM headers, the report header block, record.json, JSONL and CSV (C1-C8 via assert_outputs_consistent).
"""

from __future__ import annotations

import json

import pydicom

from app.deid.report.header import parse_header
from app.store import audit
from app.store.models.audit import AuditEvent
from tests.helpers.outputs import assert_outputs_consistent
from tests.services.conftest import Finalised, csv_rows, lines


def test_four_records_twenty_three_images(finalised_sample: Finalised) -> None:
    out = finalised_sample.root / "output"
    assert [r.status for r in finalised_sample.results] == ["finalised"] * 4
    assert len(lines(out / "records.jsonl")) == 4 and csv_rows(out / "records.csv") == 4
    assert len(lines(out / "images.jsonl")) == 23 and csv_rows(out / "images.csv") == 23
    summary = assert_outputs_consistent(out, finalised_sample.ctx)
    assert (summary.record_lines, summary.image_lines, summary.folders) == (4, 23, 4)


def test_ids_match_everywhere(finalised_sample: Finalised) -> None:
    out = finalised_sample.root / "output"
    for raw in lines(out / "records.jsonl"):
        row = json.loads(raw)
        rid, pcode, folder = row["record_id"], row["patient_code"], out / "records" / row["record_id"]
        assert (folder / "record.json").read_bytes() == raw
        for image_id in row["image_ids"]:
            ds = pydicom.dcmread(folder / f"{image_id}.dcm", stop_before_pixels=True)
            assert ds.AccessionNumber == ds.StudyID == rid
            assert str(ds.PatientID) == str(ds.PatientName) == pcode
            assert ds.ImageComments == image_id
        if row["report"]["present"]:
            head = parse_header((folder / f"{rid}_report.txt").read_text(encoding="utf-8"))
            assert head is not None and (head.record_id, head.patient_code) == (rid, pcode)
            assert list(head.image_ids) == row["image_ids"]
        assert row["qa"]["decision"] == "approved" and row["qa"]["reviewer_id"] == "u_0003"
        assert row["finding_category"] == "NORMAL" and row["cohort_ref"] == "PRE-APPROVAL"


def test_pending_folders_moved_and_audited(finalised_sample: Finalised) -> None:
    pending = finalised_sample.root / "app_data" / "work" / "pending"
    assert not any(p for p in pending.iterdir() if not p.name.startswith("."))
    with finalised_sample.ctx.db.session() as s:
        events = s.query(AuditEvent).filter(AuditEvent.action == "record.finalised").all()
        assert len(events) == 4 and audit.chain_ok(s)
        assert all(set(json.loads(e.details_json)) == {"version", "n_images", "new_images"} for e in events)
