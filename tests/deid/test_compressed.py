"""Compressed input (owner decision 6 Oct 2026, DECISIONS D-017): JPEG Lossless, JPEG Baseline and JPEG 2000
images are decoded (GDCM / Pillow), OCR-masked when their modality needs it, and written uncompressed.
Pixels that still cannot be decoded are excluded as OCR_UNCLEARED, never released unmasked.
TR-DEID-07, TR-QA-05, TR-ELIG-08."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image
from pydicom.encaps import encapsulate
from pydicom.uid import ExplicitVRLittleEndian, JPEGBaseline8Bit

from app.deid.index import index_input
from app.deid.ocr.mask import text_remaining
from app.deid.pipeline import process_study
from app.deid.types import DeidSettings
from tests.deid.helpers import burn_text, make_ds, transcode

LINES = ["DEMO PATIENT NAME 45Y/F", "DEMO-UHID-9001 01/09/2026"]


def _run(folder: Path, key: bytes, tmp: Path):  # type: ignore[no-untyped-def]
    rec = process_study(index_input(folder).studies[0], None, key, DeidSettings(), tmp / "pending")
    return rec


def _only_output(rec) -> pydicom.Dataset:  # type: ignore[no-untyped-def]
    assert rec.folder is not None
    return pydicom.dcmread(next(rec.folder.glob("*.dcm")))


def test_jpeg_lossless_cr_masked(tmp_path: Path, key: bytes) -> None:  # TR-DEID-07, TR-QA-05
    ds = make_ds(modality="CR", size=800, BodyPartExamined="CHEST")
    burn_text(ds, LINES)
    path = tmp_path / "in" / "IM0001"
    path.parent.mkdir()
    ds.save_as(path, enforce_file_format=True)
    transcode(path, "JPEGLosslessProcess14_1")
    assert str(pydicom.dcmread(path).file_meta.TransferSyntaxUID) == "1.2.840.10008.1.2.4.70"
    rec = _run(path.parent, key, tmp_path)
    assert rec.status == "awaiting_review", [str(f) for f in rec.findings]
    assert rec.ocr_regions >= 2 and not rec.excluded_images
    out = _only_output(rec)
    assert out.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian and text_remaining(out) == 0


def test_jpeg_baseline_us_masked(tmp_path: Path, key: bytes) -> None:  # TR-DEID-07, TR-QA-05
    ds = make_ds(modality="US", size=512, BodyPartExamined="ABDOMEN", PatientSex="M")
    arr8 = (ds.pixel_array / 16).astype(np.uint8)
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelData = 8, 8, 7, arr8.tobytes()
    burn_text(ds, LINES)
    buf = io.BytesIO()
    Image.fromarray(ds.pixel_array).save(buf, format="JPEG", quality=95)
    ds.PixelData = encapsulate([buf.getvalue()])
    ds["PixelData"].VR = "OB"
    ds.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
    path = tmp_path / "in" / "US0001"
    path.parent.mkdir()
    ds.save_as(path, enforce_file_format=True)
    rec = _run(path.parent, key, tmp_path)
    assert rec.status == "awaiting_review", [str(f) for f in rec.findings]
    assert rec.ocr_regions >= 2
    out = _only_output(rec)
    assert out.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian and text_remaining(out) == 0


def test_jpeg2000_ct_copied_compressed(tmp_path: Path, key: bytes) -> None:  # TR-DEID-01
    ds = make_ds(modality="CT", size=128, BodyPartExamined="CHEST")
    path = tmp_path / "in" / "CT0001"
    path.parent.mkdir()
    ds.save_as(path, enforce_file_format=True)
    transcode(path, "JPEG2000Lossless")
    source_pixels = pydicom.dcmread(path).pixel_array
    rec = _run(path.parent, key, tmp_path)
    assert rec.status == "awaiting_review", [str(f) for f in rec.findings]
    out = _only_output(rec)
    assert (
        str(out.file_meta.TransferSyntaxUID) == "1.2.840.10008.1.2.4.90"
    )  # CT is not OCR'd: pixels untouched
    assert np.array_equal(out.pixel_array, source_pixels)


def test_undecodable_pixels_excluded(tmp_path: Path, key: bytes) -> None:  # TR-ELIG-08, D-013
    ds = make_ds(modality="CR", size=64)
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 8, 8, 7
    ds.PixelData = encapsulate([b"\xff\xd8 not a real jpeg stream \xff\xd9"])
    ds["PixelData"].VR = "OB"
    ds.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
    path = tmp_path / "in" / "IM0001"
    path.parent.mkdir()
    ds.save_as(path, enforce_file_format=True)
    rec = _run(path.parent, key, tmp_path)
    assert rec.excluded_images == {"OCR_UNCLEARED": 1} and rec.record_row is None
