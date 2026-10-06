"""Image-level exclusions and the "needs OCR" rule (SPEC §6.1, §6.4 order 11). TR-ELIG-08.

Reason codes: SCREEN_SAVE_DOSE, NON_IMAGE, OCR_UNCLEARED. Remaining text after masking does NOT exclude an
image: it fails the record (A7, DECISIONS D-013).
"""

from __future__ import annotations

from pydicom.dataset import Dataset

SECONDARY_CAPTURE_PREFIX = "1.2.840.10008.5.1.4.1.1.7"
ENCAPSULATED_PREFIX = "1.2.840.10008.5.1.4.1.1.104"
OCR_MODALITIES = frozenset({"CR", "DX", "RG", "US", "MG", "XA", "RF", "SC", "OT", "ES", "XC"})
_NO_OCR_EXCLUDE = frozenset({"US", "SC", "OT"})


def modality(ds: Dataset) -> str:
    return str(ds.get("Modality", "") or "OT").upper()


def _is_sc(ds: Dataset) -> bool:
    return str(ds.get("SOPClassUID", "")).startswith(SECONDARY_CAPTURE_PREFIX)


def _burned_in(ds: Dataset) -> bool:
    return str(ds.get("BurnedInAnnotation", "")).upper() == "YES"


def exclusion_reason(ds: Dataset, study_modalities: frozenset[str]) -> str | None:
    if "PixelData" not in ds or str(ds.get("SOPClassUID", "")).startswith(ENCAPSULATED_PREFIX):
        return "NON_IMAGE"
    itype = " ".join(str(x).upper() for x in (ds.get("ImageType", []) or []))
    if "SCREEN SAVE" in itype or "DOSE" in itype:
        return "SCREEN_SAVE_DOSE"
    if _is_sc(ds) and (modality(ds) == "CT" or "CT" in study_modalities):
        return "SCREEN_SAVE_DOSE"
    return None


def needs_ocr(ds: Dataset) -> bool:
    return modality(ds) in OCR_MODALITIES or _burned_in(ds) or _is_sc(ds)


def exclude_without_ocr(ds: Dataset) -> bool:
    """With OCR off or unavailable these are never released unmasked (SPEC §6.1 A7)."""
    return modality(ds) in _NO_OCR_EXCLUDE or _burned_in(ds) or _is_sc(ds)
