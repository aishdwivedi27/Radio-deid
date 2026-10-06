"""PHI leak test for the de-identification core (CLAUDE.md rules 1, 9; SPEC §6.1 A2, T20 library outputs).

No planted identifier from the sample inbox may appear in any output header, report, record.json, row dict,
file name, raw output bytes, log line, or in OCR of the previews and the masked CR/US pixels (including the
chest CR that failed the Phase 0 baseline). TR-QA-01, TR-QA-04, TR-QA-05, TR-SEC-02.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import numpy as np
import pydicom
import pytest
from PIL import Image

from app.deid.identifiers import collect
from app.deid.index import index_input
from app.deid.ocr import engine, mask
from app.deid.qa.a_checks import check_deid
from tests.conftest import PHI
from tests.deid.conftest import Processed

# Further planted values in tests/fixtures/make_sample_data.py (names of doctors and staff, other formats).
EXTRA = [
    "Kumar",
    "Kavita",
    "Arjun",
    "Neha",
    "Rohit",
    "Meena",
    "Anil",
    "Sunil",
    "ananya.p",
    "example.com",
    "482177301192",
    "9822045671",
    "Shanti Nagar",
    "Anna Salai",
    "Civil Lines",
    "MG Road",
    "411014",
    "600002",
    "226001",
    "19680412",
    "12/04/1968",
    "19810930",
    "20070315",
    "ACC-10231",
    "ACC-55678",
    "ACC-60990",
    "ACC-60991",
    "SYNTHETIC-DEMO",
    "DEMO-UHID",
    "20260901",
    "20260905",
    "20260910",
]
PLANTED = [*PHI, *EXTRA]


def _original_uids(inbox: Path) -> list[str]:
    uids: set[str] = set()
    for g in index_input(inbox).studies:
        for f in g.files:
            ds = pydicom.dcmread(f.path, stop_before_pixels=True)
            uids |= {
                str(ds.get(k)) for k in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID") if k in ds
            }
    return sorted(uids)


def _leaks(blob: str, needles: list[str]) -> list[str]:
    low = blob.lower()
    return [n for n in needles if n.lower() in low]


def _header_text(path: Path) -> str:
    ds = pydicom.dcmread(path)
    parts = [str(el.value) for el in ds.file_meta]
    for el in ds.iterall():
        if el.VR not in ("OB", "OW", "SQ"):
            parts.append(str(el.value))
    return " ".join(parts)


def test_no_identifier_in_any_output(processed: Processed) -> None:  # T20 (library), TR-QA-04
    needles = PLANTED + _original_uids(processed.inbox)
    blob = []
    for rec in processed.records:
        blob.append(json.dumps(rec.record_row) + json.dumps(rec.image_rows) + repr(rec))
    for path in processed.pending.rglob("*"):
        if path.is_file():
            blob.append(path.name)
            blob.append(path.read_bytes().decode("latin-1"))  # raw bytes of every output file
            if path.suffix == ".dcm":
                blob.append(_header_text(path))
    assert _leaks("\n".join(blob), needles) == []


def test_pdf_metadata_doctor_absent(processed: Processed) -> None:  # TR-RPT-02
    text = " ".join(p.read_text(encoding="utf-8") for p in processed.pending.rglob("*_report.txt"))
    assert "gupta" not in text.lower() and "neha" not in text.lower()


def test_no_burned_in_text_left(processed: Processed) -> None:  # TR-QA-05, Phase 0 baseline
    checked = 0
    for path in processed.pending.rglob("*.dcm"):
        ds = pydicom.dcmread(path)
        if ds.Modality in ("CR", "US"):
            assert mask.text_remaining(ds) == 0, path.name
            checked += 1
    assert checked == 3  # chest CR, leg CR, US


def test_previews_carry_no_identifier(processed: Processed) -> None:  # TR-QA-05
    for png in processed.pending.rglob("*_preview.png"):
        image = np.asarray(Image.open(png).convert("RGB"))
        text = " ".join(t for _, t, _ in engine.run(image))
        assert _leaks(text, PLANTED) == [], png.name


def test_logs_carry_no_identifier(
    processed: Processed, key: bytes, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:  # TR-SEC-02
    from app.deid.pipeline import process_study
    from app.deid.types import DeidSettings

    caplog.set_level(logging.DEBUG)
    group = index_input(processed.inbox).studies[0]
    process_study(group, processed.matches.by_study.get(0), key, DeidSettings(), tmp_path)
    assert _leaks(caplog.text, PLANTED) == []


def test_qa_catches_planted_leaks(processed: Processed, tmp_path: Path) -> None:  # TR-QA-01, TR-QA-04
    rec = next(r for r in processed.records if r.record_row and r.record_row["report"]["present"])
    assert rec.folder is not None
    bad = tmp_path / rec.record_id
    shutil.copytree(rec.folder, bad)
    dcm = sorted(bad.glob("*.dcm"))[0]
    ds = pydicom.dcmread(dcm)
    ds.StudyDescription = "XR CHEST SHARMA"
    ds.add_new(0x00291010, "LO", "x")
    ds.PatientBirthDate = "19680412"
    ds.save_as(dcm)
    report = next(bad.glob("*_report.txt"))
    report.write_text(report.read_text(encoding="utf-8") + "\ncall 9822045671\n", encoding="utf-8")
    headers = [
        pydicom.dcmread(f.path, stop_before_pixels=True)
        for f in index_input(processed.inbox).studies[0].files
    ]
    found = {f.check for f in check_deid(bad, collect(headers))}
    assert {"A1", "A2", "A4", "A5"} <= found
