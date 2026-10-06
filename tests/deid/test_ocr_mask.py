"""Burned-in text masking and re-OCR verification (SPEC §6.1 A7 v0.4; fixtures T23-T26). TR-QA-05.

Synthetic images only. Pillow's bundled scalable font is used so the pixels are the same on every OS.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

from app.deid.ocr import detect, engine, mask

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "samples"


def _draw(
    width: int, height: int, texts: list[tuple[int, int, int, str]], rotate: bool = False
) -> np.ndarray:
    im = Image.new("L", (width, height))
    d = ImageDraw.Draw(im)
    for x, y, size, t in texts:
        d.text((x, y), t, font=ImageFont.load_default(size=size), fill=255)
    if rotate:
        im = im.rotate(180)
    return np.asarray(im) > 0


def _xray(
    texts: list[tuple[int, int, int, str]], width: int = 2800, height: int = 2300, rotate: bool = False
):  # type: ignore[no-untyped-def]
    yy, xx = np.mgrid[0:height, 0:width]
    base = 800 + 1500 * np.exp(-(((xx - width / 2) / 900) ** 2 + ((yy - height / 2) / 800) ** 2))
    m = _draw(width, height, texts, rotate)
    a = np.where(m, 4095, base).astype(np.uint16)
    x = Dataset()
    x.file_meta = FileMetaDataset()
    x.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    x.Modality, (x.Rows, x.Columns), x.SamplesPerPixel = "CR", a.shape, 1
    x.PhotometricInterpretation, x.BitsAllocated, x.BitsStored, x.HighBit = "MONOCHROME2", 16, 12, 11
    x.PixelRepresentation, x.PixelData = 0, a.tobytes()
    return x, a, m


def test_t23_small_text_on_large_image() -> None:  # T23, TR-QA-05, TR-DEID-07
    x, a, m = _xray([(40, 2250, 11, "PATEL ANANYA 9839001122")])
    r = mask.mask_dataset(x)
    assert r.regions >= 1 and r.verified
    assert (x.pixel_array[m] != a[m]).all()
    assert mask.text_remaining(x) == 0


def test_t24_name_over_anatomy_masked_and_flagged() -> None:  # T24, TR-QA-05, TR-DEID-07
    x, a, m = _xray([(1100, 1100, 40, "SHARMA RAMESH")])
    r = mask.mask_dataset(x)
    assert (x.pixel_array[m] != a[m]).all() and r.interior >= 1


def test_t25_unreadable_text_masked() -> None:  # T25, TR-QA-05
    x, a, m = _xray([(1180, 300, 40, "PATEL ANANYA UHID")], rotate=True)  # upside down: not readable
    r = mask.mask_dataset(x)
    assert r.regions >= 1 and (x.pixel_array[m] != a[m]).all()


def test_t25_low_score_box_always_masked(monkeypatch: pytest.MonkeyPatch) -> None:  # T25
    box = [[10, 10], [60, 10], [60, 30], [10, 30]]
    monkeypatch.setattr(engine, "run", lambda img: [(box, "?", 0.05), (box, "R", 0.4)])
    assert len(detect.detect(np.zeros((100, 100), np.uint8))) == 2


def test_t26_lone_marker_kept() -> None:  # T26, TR-QA-05, TR-DEID-07
    x, _, _ = _xray([(2500, 1900, 70, "R")])
    r = mask.mask_dataset(x)
    assert r.regions == 0 and r.interior == 0


def test_t26_marker_rules() -> None:  # T26
    assert detect.is_kept_marker("R", 0.95) and detect.is_kept_marker("B", 0.9)  # mirrored R read as B
    assert detect.is_kept_marker("PA", 0.85) and not detect.is_kept_marker("PA", 0.5)
    assert not detect.is_kept_marker("RAMESH", 0.99)


def test_merged_mask_has_no_staircase() -> None:  # Phase 0 baseline regression
    blocks = mask.merge_blocks([(0, 50, 400, 80), (0, 95, 330, 125), (0, 140, 380, 170)], gap=30)
    assert blocks == [(0, 50, 400, 170)]
    assert mask.merge_blocks([(0, 0, 10, 10), (500, 0, 510, 10)], gap=10) == [
        (0, 0, 10, 10),
        (500, 0, 510, 10),
    ]


def test_chest_cr_small_font_verified() -> None:  # Phase 0 baseline FAIL (10 px font on the chest CR)
    ds = pydicom.dcmread(SAMPLES / "chest_pa_RG1.dcm")
    arr = ds.pixel_array.astype(np.int32)
    lines = ["SHARMA RAMESH KUMAR 58Y/M", "UHID-778812 01/09/2026", "SUNRISE DIAGNOSTICS PUNE"]
    m = _draw(arr.shape[1], arr.shape[0], [(55, 57 + 46 * i, 13, t) for i, t in enumerate(lines)])
    arr[m] = arr.min()  # MONOCHROME1: bright text
    ds.PixelData = arr.astype(ds.pixel_array.dtype).tobytes()
    r = mask.mask_dataset(ds)
    assert r.regions >= 3 and r.verified and mask.text_remaining(ds) == 0
