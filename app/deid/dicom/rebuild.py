"""Build the de-identified DICOM dataset (SPEC §6.0, §6.1). TR-DEID-01..05, TR-DEID-03.

The rule is inverted: start from a NEW EMPTY dataset, copy in only the CORE ∪ PROFILE elements that are
present (applying each element's handling code), write the GENERATED elements, then rebuild the file meta.
The source dataset is never copied and pruned. Port of ``reference/deid_prototype/core.deid_dataset``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import (
    PYDICOM_IMPLEMENTATION_UID,
    UID,
    ExplicitVRLittleEndian,
    SecondaryCaptureImageStorage,
)

from app.deid import pseudonyms as ps
from app.deid.dicom.dates import age_from, cap_age, shift_da
from app.deid.dicom.policy import Policy, PolicyError
from app.deid.dicom.scrub import scrub
from app.deid.dicom.us_regions import rebuild_regions

APP_NAME = "DeID Station"


@dataclass(frozen=True)
class RebuildContext:
    key: bytes
    record_id: str
    patient_code: str
    image_id: str
    study_uid: str
    shift_days: int
    date_mode: str
    age_cap: int
    rx: re.Pattern[str] | None
    method: str  # DeidentificationMethod value
    masked: bool = False  # pixels were changed by OCR masking
    mask_verified: bool = False  # re-OCR found no text (BurnedInAnnotation may become NO)


def _valid_spacing(el: DataElement) -> bool:
    try:
        values = el.value if isinstance(el.value, (list, tuple)) or el.VM > 1 else [el.value]
        return len(values) > 0 and all(float(v) > 0 for v in values)
    except (TypeError, ValueError):
        return False


def _copy_one(
    out: Dataset, src: Dataset, kw: str, handling: str, policy: Policy, ctx: RebuildContext
) -> None:
    el = src[kw]
    if el.VR == "SQ" and handling != "item_allowlist":
        return  # malformed source: an allowlisted plain element encoded as a sequence is never copied
    if handling in ("copy", "mask"):
        out.add(copy.deepcopy(el))  # keeps VR and encapsulation flags; a new element, source untouched
    elif handling == "scrub":
        out.add(DataElement(el.tag, el.VR, scrub(str(el.value or ""), ctx.rx, el.VR)))
    elif handling == "validate":
        if _valid_spacing(el):
            out.add(DataElement(el.tag, el.VR, el.value))
    elif handling == "set_after_mask":
        value = "NO" if ctx.masked and ctx.mask_verified else el.value
        out.add(DataElement(el.tag, el.VR, value))
    elif handling == "item_allowlist":
        if el.VR == "SQ":
            out.add(DataElement(el.tag, "SQ", rebuild_regions(el.value, policy.us_items)))
    elif handling != "recompute":
        raise PolicyError(f"no implementation for handling code of {kw}")


def _date(src: Dataset, kw: str, ctx: RebuildContext) -> str | None:
    return shift_da(src.get(kw, ""), ctx.shift_days, ctx.date_mode)


def _series_uid(src: Dataset, ctx: RebuildContext) -> str:
    orig = str(src.get("SeriesInstanceUID", "") or f"{ctx.study_uid}/series/{src.get('SeriesNumber', '')}")
    return ps.pseudo_uid(ctx.key, orig)


def _sop_uid(src: Dataset, ctx: RebuildContext) -> str:
    return ps.pseudo_uid(ctx.key, str(src.get("SOPInstanceUID", "") or f"{ctx.study_uid}/{ctx.image_id}"))


def _frame_uid(src: Dataset, ctx: RebuildContext) -> str | None:
    orig = str(src.get("FrameOfReferenceUID", "") or "")
    return ps.pseudo_uid(ctx.key, orig) if orig else None


Generator = Callable[[Dataset, RebuildContext], "str | None"]
# Table B values, keyed by keyword. The keyword set must equal the JSON's "generated" list (checked below).
GENERATORS: dict[str, Generator] = {
    "SOPInstanceUID": _sop_uid,
    "StudyDate": lambda s, c: _date(s, "StudyDate", c),
    "SeriesDate": lambda s, c: _date(s, "SeriesDate", c),
    "AcquisitionDate": lambda s, c: _date(s, "AcquisitionDate", c),
    "ContentDate": lambda s, c: _date(s, "ContentDate", c),
    "AccessionNumber": lambda s, c: c.record_id,
    "PatientName": lambda s, c: c.patient_code,
    "PatientID": lambda s, c: c.patient_code,
    "PatientIdentityRemoved": lambda s, c: "YES",
    "DeidentificationMethod": lambda s, c: c.method,
    "StudyInstanceUID": lambda s, c: ps.pseudo_uid(c.key, c.study_uid),
    "SeriesInstanceUID": _series_uid,
    "StudyID": lambda s, c: c.record_id,
    "FrameOfReferenceUID": _frame_uid,
    "ImageComments": lambda s, c: c.image_id,
}


def build_dataset(src: Dataset, policy: Policy, ctx: RebuildContext) -> Dataset:
    if set(GENERATORS) != set(policy.generated):
        raise PolicyError("generated elements in the allowlist JSON and the code disagree")
    out = Dataset()
    for kw, handling in policy.handling.items():
        if kw in src:
            _copy_one(out, src, kw, handling, policy, ctx)
    age = cap_age(age_from(src), ctx.age_cap)
    if age and "PatientAge" in policy.handling:
        out.PatientAge = age
    for kw in policy.generated:
        value = GENERATORS[kw](src, ctx)
        if value is not None:
            setattr(out, kw, value)
    out.file_meta = _file_meta(src, out, ctx)
    return out


def _file_meta(src: Dataset, out: Dataset, ctx: RebuildContext) -> FileMetaDataset:
    """Rebuilt from scratch; the source file meta (AE titles, implementation names) is discarded."""
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = UID(str(out.get("SOPClassUID", "") or SecondaryCaptureImageStorage))
    meta.MediaStorageSOPInstanceUID = out.SOPInstanceUID
    src_ts = getattr(getattr(src, "file_meta", None), "TransferSyntaxUID", None)
    meta.TransferSyntaxUID = ExplicitVRLittleEndian if ctx.masked or src_ts is None else src_ts
    meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
    return meta


def method_text(pipeline_version: str, allowlist_version: str, date_mode: str) -> str:
    return f"{APP_NAME} {pipeline_version}; allowlist {allowlist_version}; dates {date_mode}"[:64]
