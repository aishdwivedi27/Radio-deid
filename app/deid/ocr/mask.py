"""Mask burned-in text and verify by re-OCR (SPEC §6.1 A7, v0.4). TR-QA-05.

Port of ``reference/deid_prototype/ocr_mask.mask_dataset``:
- full-resolution OCR for both passes;
- every detected box masked, widened along its text line (own width + two line-heights each side, ¼
  line-height vertically) so a neighbouring word the detector missed is covered too;
- a box centred inside the image area (beyond the outer 12% border on every side) raises review flag A7-I;
- cine: first, middle and last frames OCR'd, the union masked on every frame;
- masks on neighbouring lines merged into one block (no staircase outline for the detector to read);
- re-OCR on the masked frames; anything left fails A7 (the record, not just the image; DECISIONS D-013).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian

from app.deid.ocr.detect import Box, detect, to8

BORDER_FRACTION = 0.12
PAD = 6


@dataclass(frozen=True)
class MaskResult:
    regions: int  # boxes masked
    remaining: int  # boxes found by the re-OCR after masking
    interior: int  # masked boxes inside the image area (A7-I)

    @property
    def verified(self) -> bool:
        return self.remaining == 0


def widen(box: Box, width: int, height: int, pad: int = PAD) -> Box:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    ext = bw + 2 * bh
    vpad = max(pad, int(0.25 * bh))
    return max(0, x0 - ext), max(0, y0 - vpad), min(width, x1 + ext), min(height, y1 + vpad)


def _touch(a: Box, b: Box, gap: int) -> bool:
    overlap_x = a[0] < b[2] and b[0] < a[2]
    near_y = a[1] - gap < b[3] and b[1] - gap < a[3]
    return overlap_x and near_y


def merge_blocks(rects: list[Box], gap: int) -> list[Box]:
    """Join masks of neighbouring text lines into one solid block.

    Separate bars of different lengths form a staircase whose ends the detector reads as a glyph, so the
    re-OCR "found text" on a fully masked label (the Phase 0 chest-CR baseline failure). One rectangle has
    no such outline and also covers the gaps between lines.
    """
    blocks = list(rects)
    merged = True
    while merged:
        merged = False
        for i in range(len(blocks)):
            for j in range(i + 1, len(blocks)):
                if _touch(blocks[i], blocks[j], gap):
                    a, b = blocks[i], blocks.pop(j)
                    blocks[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    merged = True
                    break
            if merged:
                break
    return blocks


def is_interior(box: Box, width: int, height: int) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    mx, my = width * BORDER_FRACTION, height * BORDER_FRACTION
    return mx < cx < width - mx and my < cy < height - my


def _frames(ds: Dataset) -> tuple[np.ndarray, bool, list[int]]:
    arr = ds.pixel_array
    nframes = int(ds.get("NumberOfFrames", 1) or 1)
    frames = arr if nframes > 1 else arr[None, ...]
    mono1 = str(ds.get("PhotometricInterpretation", "")) == "MONOCHROME1"
    return frames, mono1, sorted({0, len(frames) // 2, len(frames) - 1})


def count_text(frames: np.ndarray, mono1: bool, sample: list[int]) -> int:
    return sum(len(detect(to8(frames[i], mono1))) for i in sample)


def text_remaining(ds: Dataset) -> int:
    """OCR detections left on a written dataset (confident markers excluded). Used by QA and tests."""
    frames, mono1, sample = _frames(ds)
    return count_text(frames, mono1, sample)


def mask_dataset(ds: Dataset) -> MaskResult:
    """Mask burned-in text in place (in memory). Never stores or returns the text itself."""
    frames, mono1, sample = _frames(ds)
    boxes: list[Box] = []
    for i in sample:
        boxes += detect(to8(frames[i], mono1))
    if not boxes:
        return MaskResult(regions=0, remaining=0, interior=0)
    arr = np.array(frames, copy=True)
    spp = int(ds.get("SamplesPerPixel", 1) or 1)
    fill = (arr.max() if mono1 else arr.min()) if spp == 1 else 0
    height, width = arr.shape[1], arr.shape[2]
    interior = sum(is_interior(b, width, height) for b in boxes)
    gap = max(b[3] - b[1] for b in boxes)  # up to one line height between lines
    for x0, y0, x1, y1 in merge_blocks([widen(b, width, height) for b in boxes], gap):
        arr[:, y0:y1, x0:x1, ...] = fill
    remaining = count_text(arr, mono1, sample)
    _store(ds, arr, nframes=len(frames), spp=spp)
    return MaskResult(regions=len(boxes), remaining=remaining, interior=interior)


def _store(ds: Dataset, arr: np.ndarray, nframes: int, spp: int) -> None:
    out = arr if nframes > 1 else arr[0]
    ds.PixelData = np.ascontiguousarray(out).tobytes()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    if spp == 3:
        ds.PhotometricInterpretation = "RGB"  # pydicom decodes YBR to RGB
        ds.PlanarConfiguration = 0
    ds.BitsAllocated = out.dtype.itemsize * 8
