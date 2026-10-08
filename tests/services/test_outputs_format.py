"""Output file format: CSV header, image-only records, schema validation, read side (SPEC §5.1, §5.2, §5.4).
TR-DEID-10, TR-REV-04, TR-COH-03, TR-RPT-05, TR-REL-NF-01, TR-WDR-03.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app.deid.record.validate import validate_row
from app.deid.types import DeidError
from app.services.context import ServiceContext
from app.services.records.append import append_rows
from app.services.records.approve import record_decision
from app.services.records.finalise import finalise
from app.services.records.query import RecordFilters, export_json_array, get_record, list_records
from tests.deid.helpers import write_study
from tests.helpers.outputs import assert_outputs_consistent
from tests.services.conftest import REVIEWER, approve_finalise, ingest_folder, lines

# Written out from SPEC §5.2 on purpose (not imported from app/deid/record/columns.py).
SPEC_RECORDS_HEADER = (
    "record_id,record_version,patient_code,modalities,body_part,study_description,study_date,date_mode,"
    "patient_sex,patient_age,manufacturer,model,n_series,n_images,image_ids,report_present,report_file,"
    "report_sha256,report_source_format,report_extraction,report_redactions_total,report_redactions,"
    "ocr_regions_masked,images_excluded_total,images_excluded,qa_auto,qa_consistency,reviewer_id,decision,"
    "decided_at,job_id,processed_at,finalised_at,pipeline_version,key_fingerprint,age_band,finding_category,"
    "consent_basis,cohort_ref,rules_version"
)
SPEC_IMAGES_HEADER = (
    "image_id,record_id,record_version,patient_code,file,sha256,bytes,modality,sop_class,series_number,"
    "instance_number,frames,rows,columns,bits_stored,photometric,pixel_spacing,view_position,laterality,"
    "body_part,transfer_syntax,burned_in_text_masked,ocr_regions,finalised_at"
)


def _finalise_one(ctx: ServiceContext, tmp_path: Path, key: bytes, name: str, report: bool = False) -> str:
    folder = tmp_path / "in" / name
    write_study(
        folder,
        2,
        modality="CT",
        patient_id=f"DEMO-F-{name}",
        accession=f"ACC-{name}",
        name="Testcase^Volunteer",
    )
    if report:
        (folder / f"ACC-{name}.txt").write_text("FINDINGS: Normal study.\n", encoding="utf-8")
    [res] = ingest_folder(ctx, folder, key)
    assert approve_finalise(ctx, res.record_id, 1).status == "finalised"
    return res.record_id


def test_csv_headers_match_spec_and_never_change(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    out = ctx.paths.output_root
    _finalise_one(ctx, tmp_path, key, "a")
    first_records, first_images = (out / "records.csv").read_bytes(), (out / "images.csv").read_bytes()
    assert first_records.split(b"\n")[0].decode() == SPEC_RECORDS_HEADER
    assert len(SPEC_RECORDS_HEADER.split(",")) == 40
    assert first_images.split(b"\n")[0].decode() == SPEC_IMAGES_HEADER
    assert len(SPEC_IMAGES_HEADER.split(",")) == 24
    _finalise_one(ctx, tmp_path, key, "b")
    records = (out / "records.csv").read_bytes()
    assert records.startswith(first_records) and records.count(SPEC_RECORDS_HEADER.encode()) == 1
    assert (out / "images.csv").read_bytes().startswith(first_images)


def test_wrong_existing_header_is_refused(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    out = ctx.paths.output_root
    out.mkdir(parents=True)
    (out / "records.csv").write_bytes(b"record_id,something_else\n")
    _finalise_one(ctx, tmp_path, key, "a")  # JSONL appended; records.csv refused
    assert (out / "records.csv").read_bytes() == b"record_id,something_else\n"
    report = append_rows(ctx)
    assert any("header differs" in m for m in report.mismatches)


def test_image_only_record(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    """v0.5: no report → C4 skipped, report columns empty, report_redactions_total = 0, no report file."""
    rid = _finalise_one(ctx, tmp_path, key, "a")
    out = ctx.paths.output_root
    [row] = list(csv.DictReader(io.StringIO((out / "records.csv").read_text("utf-8"))))
    assert row["report_present"] == "false" and row["report_redactions_total"] == "0"
    assert (
        row["report_file"]
        == row["report_sha256"]
        == row["report_source_format"]
        == row["report_extraction"]
        == ""
    )
    assert not list((out / "records" / rid).glob("*_report.txt"))
    record = json.loads(lines(out / "records.jsonl")[0])
    assert "C4" not in record["qa"]["consistency_checks"]
    assert_outputs_consistent(out, ctx)


def test_with_report_record(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    rid = _finalise_one(ctx, tmp_path, key, "a", report=True)
    [row] = list(csv.DictReader(io.StringIO((ctx.paths.output_root / "records.csv").read_text("utf-8"))))
    assert row["report_present"] == "true" and row["report_file"] == f"records/{rid}/{rid}_report.txt"
    assert row["report_source_format"] == "txt" and len(row["report_sha256"]) == 64


def test_invalid_rows_refused_before_any_write(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    folder = tmp_path / "in" / "a"
    write_study(folder, 1, modality="CT", patient_id="DEMO-F-a")
    [res] = ingest_folder(ctx, folder, key)
    with pytest.raises(DeidError):
        record_decision(ctx, res.record_id, 1, REVIEWER, "approved", "NOT_A_CATEGORY")
    record_decision(ctx, res.record_id, 1, REVIEWER, "approved", "NORMAL")
    with pytest.raises(DeidError):
        finalise(ctx, res.record_id, 1, REVIEWER, "NOT_A_CATEGORY")
    assert not ctx.paths.output_root.exists() or not any(ctx.paths.output_root.iterdir())
    with pytest.raises(DeidError) as err:
        validate_row("image", {"image_id": "Ramesh Sharma"})
    assert "Ramesh" not in str(err.value)  # paths and keywords only, never the value


def test_operator_cannot_skip_approval(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    """Nothing is appended before a reviewer approves (CLAUDE.md rule 6)."""
    folder = tmp_path / "in" / "a"
    write_study(folder, 1, modality="CT", patient_id="DEMO-F-a")
    [res] = ingest_folder(ctx, folder, key)
    with pytest.raises(DeidError):
        finalise(ctx, res.record_id, 1, REVIEWER, "NORMAL")
    assert not (ctx.paths.output_root / "records.jsonl").exists()


def test_read_side(ctx: ServiceContext, tmp_path: Path, key: bytes) -> None:
    a = _finalise_one(ctx, tmp_path, key, "a", report=True)
    b = _finalise_one(ctx, tmp_path, key, "b")
    assert {r["record_id"] for r in list_records(ctx)} == {a, b}
    assert [r["record_id"] for r in list_records(ctx, RecordFilters(has_report=True))] == [a]
    assert [r["record_id"] for r in list_records(ctx, RecordFilters(has_report=False))] == [b]
    assert list_records(ctx, RecordFilters(modality="MR")) == []
    assert (
        len(list_records(ctx, RecordFilters(modality="ct", reviewer=REVIEWER, finding_category="NORMAL")))
        == 2
    )
    item = list_records(ctx)[0]
    assert set(item) >= {"preview", "record_id", "patient_code", "n_images", "report_present", "version"}
    detail = get_record(ctx, a)
    assert (
        detail is not None
        and [v["version"] for v in detail["versions"]] == [1]
        and len(detail["images"]) == 2
    )
    assert get_record(ctx, "S000000000000") is None
    exported = json.loads(b"".join(export_json_array(ctx, "records")))
    assert [r["record_id"] for r in exported] == [
        json.loads(x)["record_id"] for x in lines(ctx.paths.output_root / "records.jsonl")
    ]
    assert len(json.loads(b"".join(export_json_array(ctx, "images")))) == 4
    assert json.loads(b"".join(export_json_array(ctx, "withdrawals"))) == []
