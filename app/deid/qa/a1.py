"""A1: strict allowlist exact-set check per modality profile (SPEC §6.0, §6.1). TR-QA-01, TR-DEID-01.

For every output file: element set ⊆ CORE ∪ PROFILE[modality] ∪ GENERATED; the always-applicable GENERATED
elements present; 0 private tags; 0 elements in groups 50xx/60xx; 0 sequences except the rebuilt ultrasound
regions sequence on US files, holding only allowed item elements. Messages name keywords, never values.
"""

from __future__ import annotations

from pydicom.dataset import Dataset
from pydicom.tag import BaseTag

from app.deid.dicom.policy import Policy, all_profile_keywords, keyword_tag, policy_for
from app.deid.types import Finding

# Generated elements that apply to every file (dates and FrameOfReferenceUID only when the source had them).
ALWAYS_GENERATED = (
    "SOPInstanceUID",
    "AccessionNumber",
    "PatientName",
    "PatientID",
    "PatientIdentityRemoved",
    "DeidentificationMethod",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "StudyID",
    "ImageComments",
)


def _label(tag: BaseTag, keyword: str) -> str:
    return keyword or f"({tag.group:04X},{tag.element:04X})"


def _foreign_profile(keyword: str, policy: Policy) -> str | None:
    for name, kws in all_profile_keywords().items():
        if name != policy.profile and keyword in kws:
            return name
    return None


def _check_items(el_value: object, policy: Policy, fname: str) -> list[Finding]:
    allowed = {keyword_tag(k) for k in policy.us_items}
    out = []
    for item in el_value:  # type: ignore[attr-defined]
        for sub in item:
            if sub.tag.is_private or sub.VR == "SQ" or int(sub.tag) not in allowed:
                out.append(
                    Finding(
                        "A1", f"element {_label(sub.tag, sub.keyword)} not allowed in a region item", fname
                    )
                )
    return out


def check_a1(ds: Dataset, fname: str) -> list[Finding]:
    policy = policy_for(str(ds.get("Modality", "") or "OT"))
    allowed = policy.allowed_tags
    out: list[Finding] = []
    for el in ds:
        label = _label(el.tag, el.keyword)
        if el.tag.is_private:
            out.append(Finding("A1", f"private element {label}", fname))
        elif el.tag.group & 0xFF00 in (0x5000, 0x6000):
            out.append(Finding("A1", f"overlay/curve element {label}", fname))
        elif int(el.tag) not in allowed:
            other = _foreign_profile(el.keyword, policy)
            why = f" (belongs to the {other} profile)" if other else ""
            out.append(Finding("A1", f"element {label} not on the allowlist{why}", fname))
        elif el.VR == "SQ":
            if el.keyword not in policy.sequence_keywords:
                out.append(Finding("A1", f"sequence {label} not allowed", fname))
            else:
                out.extend(_check_items(el.value, policy, fname))
    missing = [k for k in ALWAYS_GENERATED if k not in ds]
    if missing:
        out.append(Finding("A1", f"generated elements missing: {', '.join(missing)}", fname))
    return out
