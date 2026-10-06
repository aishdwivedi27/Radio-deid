"""``{record_id}_preview.png`` from the de-identified (masked) output. Port of ``engine._preview``."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
from PIL import Image

SIZE = 512


def write_preview(dicom_path: Path, png_path: Path) -> None:
    ds = pydicom.dcmread(dicom_path)
    a = ds.pixel_array
    if int(ds.get("NumberOfFrames", 1) or 1) > 1:
        a = a[len(a) // 2]
    if a.ndim == 3:
        a = a.mean(axis=2)
    a = a.astype(np.float32)
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    a = np.clip((a - lo) / max(float(hi - lo), 1e-6), 0, 1) * 255
    if str(ds.get("PhotometricInterpretation", "")) == "MONOCHROME1":
        a = 255 - a
    image = Image.fromarray(a.astype(np.uint8))
    image.thumbnail((SIZE, SIZE))
    image.save(png_path)  # Pillow writes no text chunks unless asked: no metadata
