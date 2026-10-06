"""Automatic de-identification checks A1-A7 on a pending folder (SPEC §6.1, §6.2 A4). TR-QA-01, TR-QA-04,
TR-QA-05, TR-DEID-04, TR-DEID-08.

A1 allowlist exact set · A2 no original identifier in any header, report, record.json or file name · A3
PatientIdentityRemoved and DeidentificationMethod · A4 no residual mobile/Aadhaar/ABHA/PAN/e-mail pattern in
the report · A5 no original date, no birth date, age capped · A6 no zero/invalid pixel spacing · A7 no text
left on OCR'd images. Findings name the check, file and field, never the value found.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pydicom
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue

from app.deid.dicom.dates import age_years
from app.deid.dicom.triage import needs_ocr
from app.deid.identifiers import identifier_regex
from app.deid.ocr import mask
from app.deid.qa.a1 import check_a1
from app.deid.report.header import RULE
from app.deid.report.redact import residual_patterns
from app.deid.types import Finding, Identifiers

CHECKS = ("A1", "A2", "A3", "A4", "A5", "A6", "A7")
AGE_CAP = 90
_BINARY_VR = {"OB", "OW", "OF", "OD", "OL", "OV", "UN"}


def _elements(ds: Dataset) -> Iterator[tuple[str, str, object]]:
    for el in ds:
        if el.VR == "SQ":
            for item in el.value:
                yield from _elements(item)
        elif el.VR not in _BINARY_VR:
            yield el.keyword or str(el.tag), el.VR, el.value


def _header_checks(ds: Dataset, name: str, ids: Identifiers) -> list[Finding]:
    rx = identifier_regex(ids.values)
    out = check_a1(ds, name)
    for kw, vr, value in _elements(ds):
        if rx and rx.search(str(value)):
            out.append(Finding("A2", f"{kw} contains an original identifier", name))
        if vr == "DA" and str(value) in ids.dates:
            out.append(Finding("A5", f"{kw} holds an original date", name))
    if str(ds.get("PatientIdentityRemoved", "")) != "YES" or not ds.get("DeidentificationMethod"):
        out.append(Finding("A3", "identity-removed flag or method missing", name))
    if "PatientBirthDate" in ds:
        out.append(Finding("A5", "PatientBirthDate present", name))
    years = age_years(str(ds.get("PatientAge", "") or ""))
    if years is not None and years > AGE_CAP:
        out.append(Finding("A5", "PatientAge above the 90-year cap", name))
    for kw in ("PixelSpacing", "ImagerPixelSpacing"):
        if kw in ds and not _positive(ds[kw].value):
            out.append(Finding("A6", f"invalid {kw}", name))
    return out


def _positive(value: Any) -> bool:
    try:
        values = list(value) if isinstance(value, (list, tuple, MultiValue)) else [value]
        return bool(values) and all(float(v) > 0 for v in values)
    except (TypeError, ValueError):
        return False


def _a7(ds: Dataset, name: str, remaining: Mapping[str, int] | None) -> list[Finding]:
    if not needs_ocr(ds):
        return []
    left = remaining[name] if remaining is not None and name in remaining else mask.text_remaining(ds)
    return [Finding("A7", "text still detected after masking", name)] if left else []


def _report_checks(folder: Path, ids: Identifiers) -> list[Finding]:
    out: list[Finding] = []
    rx = identifier_regex(ids.values)
    for path in sorted(folder.glob("*_report.txt")):
        text = path.read_text(encoding="utf-8")
        body = text.split(RULE + "\n", 1)[-1]
        if rx and rx.search(text):
            out.append(Finding("A2", "original identifier in the report", path.name))
        out += [
            Finding("A4", f"residual {name} pattern in the report", path.name)
            for name in residual_patterns(body)
        ]
    record = folder / "record.json"
    if record.exists() and rx and rx.search(record.read_text(encoding="utf-8")):
        out.append(Finding("A2", "original identifier in record.json", record.name))
    if rx:
        out += [
            Finding("A2", "original identifier in a file name", "")
            for p in folder.iterdir()
            if rx.search(p.name)
        ]
    return out


def check_deid(
    pending_dir: Path, identifiers: Identifiers, ocr_remaining: Mapping[str, int] | None = None
) -> list[Finding]:
    """A1-A7 over every output file. ``ocr_remaining`` (file name -> boxes left after masking) lets the
    pipeline reuse its own re-OCR; without it the images are OCR'd again here."""
    findings: list[Finding] = []
    for path in sorted(pending_dir.glob("*.dcm")):
        ds = pydicom.dcmread(path)
        findings += _header_checks(ds, path.name, identifiers)
        findings += _a7(ds, path.name, ocr_remaining)
    return findings + _report_checks(pending_dir, identifiers)


def status(findings: list[Finding], not_applicable: set[str]) -> dict[str, str]:
    failed = {f.check for f in findings}
    return {c: "n/a" if c in not_applicable else ("fail" if c in failed else "pass") for c in CHECKS}
