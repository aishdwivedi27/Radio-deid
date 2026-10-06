"""Consistency checks C1-C8: the IDs match everywhere (SPEC §3.1, §3.2). TR-QA-02.

C4 and the report part of C6 apply only when the record has a report (v0.5). ``finalised`` maps finalised
record_ids and image_ids to the study_key of the study they belong to (from the DB, Phase 2+), for C7.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pydicom

from app.deid.record.canonical import json_line, sha256_file
from app.deid.report.header import parse_header
from app.deid.types import Finding, PendingRecord

CHECKS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")


def _c2_c3(folder: Path, rid: str, pcode: str) -> list[Finding]:
    out = []
    for path in sorted(folder.glob("*.dcm")):
        ds = pydicom.dcmread(path, stop_before_pixels=True)
        if not (ds.get("AccessionNumber") == ds.get("StudyID") == rid):
            out.append(Finding("C2", "AccessionNumber/StudyID differ from record_id", path.name))
        if not (str(ds.get("PatientID", "")) == str(ds.get("PatientName", "")) == pcode):
            out.append(Finding("C2", "PatientID/PatientName differ from patient_code", path.name))
        if str(ds.get("ImageComments", "")) != path.stem or not path.stem.startswith(rid + "-"):
            out.append(Finding("C3", "ImageComments, file name and image_id differ", path.name))
    return out


def _c4(folder: Path, rid: str, pcode: str, stems: set[str]) -> list[Finding]:
    path = folder / f"{rid}_report.txt"
    if not path.exists():
        return [Finding("C4", "report file missing or misnamed", "")]
    head = parse_header(path.read_text(encoding="utf-8"))
    if head is None:
        return [Finding("C4", "report header block malformed", path.name)]
    out = []
    if head.record_id != rid or head.patient_code != pcode:
        out.append(Finding("C4", "report header IDs differ from the record", path.name))
    if set(head.image_ids) != stems or len(head.image_ids) != len(stems):
        out.append(Finding("C4", "report Images list differs from the image files", path.name))
    return out


def _expected_files(rec: PendingRecord, stems: set[str]) -> set[str]:
    names = {f"{s}.dcm" for s in stems} | {f"{rec.record_id}_preview.png", "record.json"}
    report = (rec.record_row or {}).get("report", {})
    if report.get("present"):
        names.add(f"{rec.record_id}_report.txt")
    return names


def _c5(folder: Path, rec: PendingRecord, stems: set[str]) -> list[Finding]:
    out = []
    rows = {r["image_id"]: r for r in rec.image_rows}
    if set(rows) != stems or len(rec.image_rows) != len(stems):
        out.append(Finding("C5", "image rows and image files differ (IDs or count)", ""))
    for stem in sorted(stems & set(rows)):
        row, path = rows[stem], folder / f"{stem}.dcm"
        if row["sha256"] != sha256_file(path) or row["file"] != f"records/{rec.record_id}/{path.name}":
            out.append(Finding("C5", "image row SHA-256 or file path differs", path.name))
    extra = {p.name for p in folder.iterdir()} - _expected_files(rec, stems)
    if extra:
        out.append(Finding("C5", f"{len(extra)} unexpected file(s) in the record folder", ""))
    return out


def _c6(folder: Path, rec: PendingRecord, row: dict[str, Any]) -> list[Finding]:
    out = []
    ids = [r["image_id"] for r in rec.image_rows]
    if row.get("n_images") != len(ids) or row.get("image_ids") != ids:
        out.append(Finding("C6", "record row n_images/image_ids differ from the image rows", ""))
    report = row.get("report", {})
    if report.get("present"):
        path = folder / f"{rec.record_id}_report.txt"
        if not path.exists() or report.get("sha256") != sha256_file(path):
            out.append(Finding("C6", "record row report.sha256 differs from the report file", ""))
    return out


def _c7(rec: PendingRecord, study_key: str, finalised: Mapping[str, str]) -> list[Finding]:
    ids = [rec.record_id, *(r["image_id"] for r in rec.image_rows)]
    clashes = [i for i in ids if i in finalised and finalised[i] != study_key]
    return (
        [Finding("C7", f"{len(clashes)} ID(s) collide with a finalised row of another study", "")]
        if clashes
        else []
    )


def check_consistency(
    pending_dir: Path, rec: PendingRecord, finalised: Mapping[str, str] | None = None
) -> list[Finding]:
    rid, pcode, row = rec.record_id, rec.patient_code, rec.record_row or {}
    stems = {p.stem for p in pending_dir.glob("*.dcm")}
    out = [] if pending_dir.name == rid else [Finding("C1", "folder name differs from record_id", "")]
    out += _c2_c3(pending_dir, rid, pcode)
    if row.get("report", {}).get("present"):
        out += _c4(pending_dir, rid, pcode, stems)
    out += _c5(pending_dir, rec, stems)
    out += _c6(pending_dir, rec, row)
    out += _c7(rec, rec.study_key, finalised or {})
    record_json = pending_dir / "record.json"
    if not record_json.exists() or record_json.read_bytes() != json_line(row):
        out.append(Finding("C8", "record.json is not byte-identical to the row to append", ""))
    return out


def status(findings: list[Finding], report_present: bool) -> dict[str, str]:
    failed = {f.check for f in findings}
    checks = [c for c in CHECKS if report_present or c != "C4"]
    return {c: "fail" if c in failed else "pass" for c in checks}
