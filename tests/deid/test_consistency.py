"""Consistency checks C1-C8 on pending records, and tamper detection (SPEC §3.1, §3.2; T31). TR-QA-02,
TR-DEID-03, TR-RPT-05."""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pydicom
import pytest

from app.deid.index import index_input
from app.deid.pipeline import process_study
from app.deid.qa.c_checks import check_consistency
from app.deid.types import DeidSettings, PendingRecord
from tests.deid.conftest import Processed


def _copy(rec: PendingRecord, tmp: Path, name: str | None = None) -> tuple[Path, PendingRecord]:
    assert rec.folder is not None
    dest = tmp / (name or rec.record_id)
    shutil.copytree(rec.folder, dest)
    return dest, dataclasses.replace(rec, image_rows=[dict(r) for r in rec.image_rows])


def _checks(folder: Path, rec: PendingRecord, finalised: dict[str, str] | None = None) -> set[str]:
    return {f.check for f in check_consistency(folder, rec, finalised)}


def _with_report(p: Processed) -> PendingRecord:
    return next(r for r in p.records if r.record_row and r.record_row["report"]["present"])


def test_sample_inbox_all_pass(processed: Processed) -> None:  # TR-QA-02, acceptance 1
    assert len(processed.records) == 4
    assert all(r.status == "awaiting_review" for r in processed.records), [
        str(f) for r in processed.records for f in r.findings
    ]
    for rec in processed.records:
        assert rec.folder and rec.folder.name == rec.record_id
        assert _checks(rec.folder, rec) == set()
        assert set(rec.record_row["qa"]["consistency_checks"].values()) == {"pass"}  # type: ignore[index]
        assert set(rec.record_row["qa"]["auto_checks"].values()) <= {"pass", "n/a"}  # type: ignore[index]
    assert sum(len(r.image_rows) for r in processed.records) == 23


def test_ids_written_everywhere(processed: Processed) -> None:  # TR-DEID-03
    rec = _with_report(processed)
    assert rec.folder is not None
    for row in rec.image_rows:
        ds = pydicom.dcmread(rec.folder / f"{row['image_id']}.dcm", stop_before_pixels=True)
        assert ds.AccessionNumber == ds.StudyID == rec.record_id
        assert ds.PatientID == ds.PatientName == rec.patient_code and ds.ImageComments == row["image_id"]
    report = (rec.folder / f"{rec.record_id}_report.txt").read_text(encoding="utf-8")
    assert f"Record ID: {rec.record_id}\nPatient code: {rec.patient_code}\n" in report
    assert (rec.folder / f"{rec.record_id}_preview.png").exists()


def test_tamper_rename_file(processed: Processed, tmp_path: Path) -> None:  # TR-QA-02
    folder, rec = _copy(processed.records[1], tmp_path)
    first = sorted(folder.glob("*.dcm"))[0]
    first.rename(folder / f"{rec.record_id}-0009-000009.dcm")
    assert {"C3", "C5"} <= _checks(folder, rec)


def test_tamper_image_comments(processed: Processed, tmp_path: Path) -> None:  # TR-QA-02
    folder, rec = _copy(processed.records[0], tmp_path)
    path = sorted(folder.glob("*.dcm"))[0]
    ds = pydicom.dcmread(path)
    ds.ImageComments = rec.record_id + "-0001-000099"
    ds.save_as(path)
    assert "C3" in _checks(folder, rec)


def test_tamper_report_header(processed: Processed, tmp_path: Path) -> None:  # TR-QA-02
    folder, rec = _copy(_with_report(processed), tmp_path)
    path = folder / f"{rec.record_id}_report.txt"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Record ID: S", "Record ID: X"), encoding="utf-8"
    )
    assert {"C4", "C6"} <= _checks(folder, rec)


def test_tamper_drop_image_row(processed: Processed, tmp_path: Path) -> None:  # TR-QA-02
    folder, rec = _copy(processed.records[1], tmp_path)
    rec.image_rows = rec.image_rows[1:]
    assert {"C5", "C6"} <= _checks(folder, rec)


def test_tamper_record_json_and_folder(processed: Processed, tmp_path: Path) -> None:  # TR-QA-02
    folder, rec = _copy(processed.records[0], tmp_path)
    (folder / "record.json").write_bytes((folder / "record.json").read_bytes() + b" ")
    assert "C8" in _checks(folder, rec)
    other, rec2 = _copy(processed.records[0], tmp_path, name="S000000000000")
    assert "C1" in _checks(other, rec2)


def test_tamper_header_ids(processed: Processed, tmp_path: Path) -> None:  # TR-QA-02
    folder, rec = _copy(processed.records[0], tmp_path)
    path = sorted(folder.glob("*.dcm"))[0]
    ds = pydicom.dcmread(path)
    ds.PatientID = "P000000000000"
    ds.save_as(path)
    assert "C2" in _checks(folder, rec)


def test_collision_with_finalised_study(processed: Processed) -> None:  # TR-QA-02 (C7)
    rec = processed.records[0]
    assert rec.folder is not None
    assert "C7" in _checks(rec.folder, rec, {rec.record_id: "another-study-key"})
    assert "C7" not in _checks(rec.folder, rec, {rec.record_id: rec.study_key})


@pytest.mark.parametrize("which", [0])
def test_t31_image_only_record(processed: Processed, key: bytes, tmp_path: Path, which: int) -> None:
    # T31 (library part), TR-RPT-05, TR-QA-02: C4 and the report part of C6 are not applied
    group = index_input(processed.inbox).studies[which]
    rec = process_study(group, None, key, DeidSettings(job_id="J-TEST"), tmp_path)
    assert rec.status == "awaiting_review" and rec.folder is not None
    row = rec.record_row
    assert row is not None and row["report"] == {
        "present": False,
        "file": None,
        "sha256": None,
        "redactions": {},
        "source_format": None,
        "extraction": None,
    }
    assert not list(rec.folder.glob("*_report.txt"))
    assert "C4" not in row["qa"]["consistency_checks"] and row["qa"]["auto_checks"]["A4"] == "n/a"
    assert _checks(rec.folder, rec) == set()


def test_rerun_is_deterministic(processed: Processed, key: bytes, tmp_path: Path) -> None:  # TR-DEID-03
    index = index_input(processed.inbox)
    rec = process_study(index.studies[1], processed.matches.by_study.get(1), key, DeidSettings(), tmp_path)
    before = processed.records[1]
    assert rec.record_id == before.record_id and [r["image_id"] for r in rec.image_rows] == [
        r["image_id"] for r in before.image_rows
    ]
    assert [r["sha256"] for r in rec.image_rows] == [r["sha256"] for r in before.image_rows]
