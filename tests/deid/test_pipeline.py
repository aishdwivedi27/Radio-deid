"""process_study paths beyond the sample inbox (SPEC §4, §4.1, §6.1, §6.4). TR-COH-05, TR-ELIG-01,
TR-ELIG-08, TR-QA-05."""

from __future__ import annotations

from pathlib import Path

from app.deid.index import index_input
from app.deid.pipeline import process_study, screen_study
from app.deid.types import DeidSettings, ScreenContext
from tests.deid.helpers import write_study


def _group(folder: Path):  # type: ignore[no-untyped-def]
    return index_input(folder).studies[0]


def test_excluded_study_writes_nothing(tmp_path: Path, key: bytes) -> None:  # TR-ELIG-01, TR-COH-05
    write_study(tmp_path / "in", InstitutionName="City Hospital", PatientID="UHID-1")
    pending = tmp_path / "pending"
    pending.mkdir()
    rec = process_study(_group(tmp_path / "in"), None, key, DeidSettings(), pending)
    assert rec.status == "excluded" and rec.screen and rec.screen.reason == "PRE_APPROVAL_REAL_DATA"
    assert rec.folder is None and list(pending.iterdir()) == []


def test_under_18_excluded_in_pipeline(tmp_path: Path, key: bytes) -> None:  # T1, TR-ELIG-01
    write_study(tmp_path / "in", PatientAge="016Y")
    group = _group(tmp_path / "in")
    assert screen_study(group, None, key, ScreenContext()).reason == "AGE_UNDER_18"
    rec = process_study(group, None, key, DeidSettings(), tmp_path / "p")
    assert rec.status == "excluded" and rec.folder is None


def test_ocr_off_excludes_ultrasound(tmp_path: Path, key: bytes) -> None:  # TR-QA-05, TR-ELIG-08
    write_study(tmp_path / "in", modality="US", BodyPartExamined="ABDOMEN")
    rec = process_study(_group(tmp_path / "in"), None, key, DeidSettings(ocr_enabled=False), tmp_path / "p")
    assert rec.excluded_images == {"OCR_UNCLEARED": 1}
    assert rec.status == "auto_qa_failed" and [f.check for f in rec.findings] == ["NO_IMAGES"]
    assert not any((tmp_path / "p").iterdir())


def test_ocr_off_ct_released_a7_not_applicable(tmp_path: Path, key: bytes) -> None:  # TR-QA-05
    write_study(tmp_path / "in", n=2, modality="CT", BodyPartExamined="CHEST")
    rec = process_study(_group(tmp_path / "in"), None, key, DeidSettings(ocr_enabled=False), tmp_path / "p")
    assert rec.status == "awaiting_review" and rec.record_row is not None
    assert rec.record_row["qa"]["auto_checks"]["A7"] == "n/a" and rec.record_row["n_images"] == 2


def test_only_a_dose_screen(tmp_path: Path, key: bytes) -> None:  # TR-ELIG-08
    write_study(tmp_path / "in", modality="CT", ImageType=["DERIVED", "SECONDARY", "SCREEN SAVE"])
    rec = process_study(_group(tmp_path / "in"), None, key, DeidSettings(), tmp_path / "p")
    assert rec.excluded_images == {"SCREEN_SAVE_DOSE": 1} and rec.record_row is None


def test_truncated_series_number_noted(tmp_path: Path, key: bytes) -> None:  # TR-DEID-03, D-016
    write_study(tmp_path / "in", series=12345, BodyPartExamined="CHEST")
    rec = process_study(_group(tmp_path / "in"), None, key, DeidSettings(), tmp_path / "p")
    assert rec.status == "awaiting_review" and rec.notes["ID_TRUNCATED"] == 1
    row = rec.image_rows[0]
    assert row["image_id"].endswith("-2345-000001") and row["series_number"] == 12345
