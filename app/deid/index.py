"""Find DICOM studies and reports in an input folder (SPEC §6.1 last paragraph, §6.5). TR-ING-03.

DICOM is recognised by content (``DICM`` at byte 128), not by extension; ``DICOMDIR`` is skipped. Only the
header is read. The input folder is never modified.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError

from app.deid.types import InputIndex, SourceFile, StudyGroup

REPORT_SUFFIXES = {".txt", ".docx", ".pdf"}
DICOMDIR_SOP_CLASS = "1.2.840.10008.1.3.10"
_HEADER_KEYWORDS = [
    "StudyInstanceUID", "SOPInstanceUID", "PatientID", "AccessionNumber", "SeriesNumber", "InstanceNumber",
]


def is_dicom(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(132)
    except OSError:
        return False
    return len(head) == 132 and head[128:132] == b"DICM"


def _as_int(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _classify(path: Path) -> str:
    if path.name.startswith("."):
        return "hidden"
    if path.name.upper() == "DICOMDIR":
        return "dicomdir"
    if is_dicom(path):
        return "dicom"
    if path.suffix.lower() in REPORT_SUFFIXES:
        return "report"
    return "non_dicom"


def index_input(input_dir: Path) -> InputIndex:
    studies: dict[str, StudyGroup] = {}
    reports: list[Path] = []
    skipped: Counter[str] = Counter()
    for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        kind = _classify(path)
        if kind == "report":
            reports.append(path)
            continue
        if kind != "dicom":
            skipped[kind] += 1
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_HEADER_KEYWORDS)
        except (InvalidDicomError, OSError, ValueError, EOFError):
            skipped["unreadable"] += 1
            continue
        if str(ds.file_meta.get("MediaStorageSOPClassUID", "")) == DICOMDIR_SOP_CLASS:
            skipped["dicomdir"] += 1
            continue
        uid = str(ds.get("StudyInstanceUID", "") or "") or f"folder:{path.parent}"
        group = studies.setdefault(uid, StudyGroup(
            study_uid=uid,
            patient_id=str(ds.get("PatientID", "") or ""),
            accession=str(ds.get("AccessionNumber", "") or ""),
        ))
        group.files.append(SourceFile(
            path=path,
            sop_uid=str(ds.get("SOPInstanceUID", "") or path.name),
            series_number=_as_int(ds.get("SeriesNumber")),
            instance_number=_as_int(ds.get("InstanceNumber")),
        ))
        group.folders.add(path.parent)
    return InputIndex(studies=list(studies.values()), reports=reports, skipped=skipped)
