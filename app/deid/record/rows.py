"""Record row and image rows as they will be appended (SPEC §5.2; record/image JSON schemas). TR-DEID-10.

Rows are built only from the de-identified output files, never from the original headers. Reviewer fields
(``qa.decision``, ``qa.reviewer_id``, ``qa.decided_at``, ``finalised_at``, ``finding_category``,
``cohort_ref``) are added at finalisation (``finalrow.py``).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydicom.dataset import Dataset

from app.deid.dicom.dates import age_band, iso_date
from app.deid.record.canonical import sha256_file
from app.deid.types import ExtractedReport, ScreenResult

SEXES = {"M", "F", "O"}


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _spacing(ds: Dataset) -> list[float] | None:
    for kw in ("PixelSpacing", "ImagerPixelSpacing"):
        if kw in ds:
            values = [float(v) for v in ds[kw].value]
            if len(values) == 2 and all(v > 0 for v in values):
                return values
    return None


def image_row(
    path: Path, ds: Dataset, rid: str, pcode: str, ocr_regions: int, version: int = 1
) -> dict[str, Any]:
    return {
        "image_id": path.stem,
        "record_id": rid,
        "record_version": version,
        "patient_code": pcode,
        "file": f"records/{rid}/{path.name}",
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "modality": str(ds.get("Modality", "") or ""),
        "sop_class": str(ds.get("SOPClassUID", "") or ""),
        "series_number": _int(ds.get("SeriesNumber")),
        "instance_number": _int(ds.get("InstanceNumber")),
        "frames": max(_int(ds.get("NumberOfFrames"), 1), 1),
        "rows": _int(ds.get("Rows")),
        "columns": _int(ds.get("Columns")),
        "bits_stored": _int(ds.get("BitsStored")),
        "photometric": str(ds.get("PhotometricInterpretation", "") or ""),
        "pixel_spacing": _spacing(ds),
        "view_position": str(ds.get("ViewPosition", "") or ""),
        "laterality": str(ds.get("Laterality", "") or ds.get("ImageLaterality", "") or ""),
        "body_part": str(ds.get("BodyPartExamined", "") or ""),
        "transfer_syntax": str(ds.file_meta.TransferSyntaxUID),
        "burned_in_text_masked": ocr_regions > 0,
        "ocr_regions": ocr_regions,
    }


@dataclass(frozen=True)
class ReportPart:
    present: bool
    file: Path | None = None
    source: ExtractedReport | None = None
    redactions: dict[str, int] | None = None

    def as_row(self, rid: str) -> dict[str, Any]:
        if not self.present or self.file is None or self.source is None:
            return {
                "present": False,
                "file": None,
                "sha256": None,
                "redactions": {},
                "source_format": None,
                "extraction": None,
            }
        return {
            "present": True,
            "file": f"records/{rid}/{self.file.name}",
            "sha256": sha256_file(self.file),
            "redactions": dict(self.redactions or {}),
            "source_format": self.source.source_format,
            "extraction": self.source.extraction,
        }


@dataclass(frozen=True)
class RecordInputs:
    rid: str
    pcode: str
    first: Dataset  # first written (de-identified) dataset
    image_rows: list[dict[str, Any]]
    report: ReportPart
    excluded: Counter[str]
    screen: ScreenResult
    date_mode: str
    job_id: str
    pipeline_version: str
    key_fingerprint: str
    processed_at: str = ""
    version: int = 1


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def record_row(inp: RecordInputs, auto: dict[str, str], consistency: dict[str, str]) -> dict[str, Any]:
    first, rows = inp.first, inp.image_rows
    sex = str(first.get("PatientSex", "") or "").upper()
    age = str(first.get("PatientAge", "") or "")
    return {
        "record_id": inp.rid,
        "record_version": inp.version,
        "patient_code": inp.pcode,
        "modalities": sorted({r["modality"] for r in rows if r["modality"]}),
        "body_part": str(first.get("BodyPartExamined", "") or ""),
        "study_description": str(first.get("StudyDescription", "") or ""),
        "study_date": iso_date(str(first.get("StudyDate", "") or ""), inp.date_mode),
        "date_mode": inp.date_mode,
        "patient_sex": sex if sex in SEXES else "",
        "patient_age": age,
        "manufacturer": str(first.get("Manufacturer", "") or ""),
        "model": str(first.get("ManufacturerModelName", "") or ""),
        "n_series": len({r["series_number"] for r in rows}),
        "n_images": len(rows),
        "image_ids": [r["image_id"] for r in rows],
        "report": inp.report.as_row(inp.rid),
        "ocr_regions_masked": sum(r["ocr_regions"] for r in rows),
        "images_excluded": [{"reason": k, "count": v} for k, v in sorted(inp.excluded.items())],
        "qa": {"auto_checks": auto, "consistency_checks": consistency},
        "job_id": inp.job_id,
        "processed_at": inp.processed_at or now_iso(),
        "pipeline_version": inp.pipeline_version,
        "key_fingerprint": inp.key_fingerprint,
        "age_band": age_band(age),
        "consent_basis": inp.screen.consent_basis,
        "rules_version": inp.screen.rules_version,
    }
