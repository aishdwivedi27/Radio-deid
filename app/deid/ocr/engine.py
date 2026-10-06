"""RapidOCR engine (ONNX, CPU, models bundled with ``rapidocr_onnxruntime``; no download at runtime).

Full resolution (SPEC §6.1 A7): RapidOCR shrinks images above ``max_side_len`` (default 2000 px), which made
the prototype miss 10-11 px text, so the limit is lifted. Every detected box is returned (``text_score=0``).
"""

from __future__ import annotations

import importlib.util
from typing import Any

import numpy as np

MAX_SIDE = 10000
_engine: Any = None


def available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


def run(image: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
    """OCR an RGB uint8 image. Returns (box points, text, score) for every detected box."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR(max_side_len=MAX_SIDE, text_score=0.0)
    result, _ = _engine(image, text_score=0.0)
    return [(box, str(text), float(score or 0)) for box, text, score in (result or [])]


_doc_engine: Any = None
DOC_TEXT_SCORE = 0.5


def run_document(image: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
    """OCR a scanned report page (SPEC §6.2). Unlike burned-in text masking, the goal here is to READ the
    text: default detector size, no text-angle classifier (it flipped whole report lines in testing) and
    only confidently read boxes. Unread text cannot leak; the reviewer sees an OCR banner."""
    global _doc_engine
    if _doc_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _doc_engine = RapidOCR()
    result, _ = _doc_engine(image, use_cls=False, text_score=DOC_TEXT_SCORE)
    return [(box, str(text), float(score or 0)) for box, text, score in (result or [])]
