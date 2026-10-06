"""Text boxes to mask (SPEC §6.1 A7). Port of ``reference/deid_prototype/ocr_mask._detect`` / ``_to8``.

Every box the detector finds is masked whatever its recognition score. Recognition confidence is used only
to KEEP a positional marker (R, L, PA, AP, LAT …) or a lone character (a mirrored lead marker) read with
confidence ≥ 0.8. The recognised text itself is never stored or returned.
"""

from __future__ import annotations

import re

import numpy as np

from app.deid.ocr import engine

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)

KEEP_TOKENS = frozenset(
    "R L RT LT PA AP LAT LATERAL ERECT SUPINE PRONE UPRIGHT INSP EXP PORTABLE DECUB OBL OBLIQUE "
    "LAO RAO LPO RPO H F A P".split()
)
KEEP_MIN_SCORE = 0.8


def to8(frame: np.ndarray, mono1: bool) -> np.ndarray:
    """Window a frame to 8 bits for OCR (robust percentiles; MONOCHROME1 inverted so text is bright)."""
    f = frame.astype(np.float32)
    if f.ndim == 3:
        f = f.mean(axis=2)
    lo, hi = np.percentile(f, 0.5), np.percentile(f, 99.8)
    f = np.clip((f - lo) / max(float(hi - lo), 1e-6), 0, 1) * 255
    if mono1:
        f = 255 - f
    out: np.ndarray = f.astype(np.uint8)
    return out


def is_kept_marker(text: str, score: float) -> bool:
    token = re.sub(r"[^A-Z0-9]", "", text.upper())
    return (token in KEEP_TOKENS or len(token) == 1) and score >= KEEP_MIN_SCORE


def detect(frame8: np.ndarray) -> list[Box]:
    """Boxes of all detected text at full resolution, minus confidently read markers."""
    boxes: list[Box] = []
    for points, text, score in engine.run(np.stack([frame8] * 3, axis=-1)):
        if is_kept_marker(text, score):
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        boxes.append((int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1))
    return boxes
