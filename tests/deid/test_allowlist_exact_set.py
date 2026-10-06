"""Strict allowlist: exact-set check per modality profile (SPEC §6.0, §6.1 A1, A01 fixtures).

TR-DEID-01, TR-DEID-02, TR-DEID-06, TR-QA-01, TR-CC-01.
"""

from __future__ import annotations

from pathlib import Path

import pydicom
import pytest
from pydicom.datadict import DicomDictionary, keyword_for_tag
from pydicom.dataset import Dataset
from pydicom.filebase import DicomBytesIO
from pydicom.filewriter import write_dataset
from pydicom.sequence import Sequence

from app.deid.dicom.policy import keyword_tag, policy_for
from app.deid.dicom.rebuild import RebuildContext, build_dataset
from app.deid.identifiers import collect, identifier_regex
from app.deid.qa.a1 import check_a1
from app.deid.schemas import load_allowlist

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "samples" / "chest_pa_RG1.dcm"
PLANTED = "Sharma^Ramesh"
_STR = {
    "AS": "030Y",
    "DA": "20200101",
    "DS": "1",
    "IS": "1",
    "TM": "101500",
    "DT": "20200101101500",
    "UI": "1.2.3.4",
}
_NUM = {"US", "SS", "UL", "SL", "FL", "FD", "UV", "SV"}
_BIN = {"OB", "OW", "OF", "OD", "OL", "OV", "UN"}


def _dummy(vr: str) -> object:
    if vr in _NUM:
        return 1
    if vr in _BIN:
        return b"\x00\x00"
    if vr == "AT":
        return 0x00100010
    if vr == "SQ":
        item = Dataset()
        item.PatientName = PLANTED
        inner = Dataset()
        inner.PatientID = "DEMO-UHID-778812"
        item.add_new(0x00081115, "SQ", Sequence([inner]))  # nested sequence
        return Sequence([item])
    return _STR.get(vr, "X")


def _writable(tag: int, vr: str, value: object) -> bool:
    ds = Dataset()
    ds.add_new(tag, vr, value)
    fp = DicomBytesIO()
    fp.is_little_endian, fp.is_implicit_VR = True, False
    try:
        write_dataset(fp, ds)
    except Exception:  # noqa: BLE001 - probing which dummy values pydicom can encode
        return False
    return True


def _all_allowed_tags() -> set[int]:
    al = load_allowlist()
    entries = [*al.core, *al.generated, *(e for p in al.profiles.values() for e in p)]
    return {keyword_tag(e["keyword"]) for e in entries}


def flood(ds: Dataset) -> int:
    """Add every other dictionary element pydicom can write, 20 private tags, an overlay and a curve."""
    skip = _all_allowed_tags()
    added = 0
    for tag, entry in DicomDictionary.items():
        vr = entry[0]
        if tag in skip or tag in ds or " or " in vr or (tag >> 16) in (0x0002, 0x7FE0):
            continue
        if (tag >> 16) & 0xFF00 in (0x5000, 0x6000):
            continue
        value = _dummy(vr)
        if _writable(tag, vr, value):
            ds.add_new(tag, vr, value)
            added += 1
    block = ds.private_block(0x0029, "DEMO PLANT", create=True)
    for i in range(20):
        block.add_new(0x10 + i, "LO", f"Ramesh {i}")
    ds.add_new(0x60000010, "US", 8)  # overlay rows
    ds.add_new(0x60003000, "OW", b"\x00" * 8)  # overlay data
    ds.add_new(0x50000005, "US", 1)  # curve dimensions
    return added + 23


def _ctx(ds: Dataset) -> RebuildContext:
    rx = identifier_regex(collect([ds]).values)
    return RebuildContext(
        key=b"k" * 32,
        record_id="S0123456789AB",
        patient_code="P0123456789AB",
        image_id="S0123456789AB-0002-000001",
        study_uid=str(ds.StudyInstanceUID),
        shift_days=10,
        date_mode="shift",
        age_cap=90,
        rx=rx,
        method="test",
    )


def _source(modality: str) -> Dataset:
    ds = pydicom.dcmread(SAMPLE)
    ds.Modality = modality
    ds.PatientName, ds.PatientID = PLANTED, "DEMO-UHID-778812"
    ds.StudyInstanceUID = "1.2.826.0.1.3680043.2.1125.1"
    return ds


def test_allowlist_counts_pinned() -> None:  # TR-CC-01
    al = load_allowlist()
    assert len(al.core) == 54 and len(al.generated) == 15
    assert {k: len(v) for k, v in al.profiles.items()} == {"CT": 4, "MR": 14, "US": 4, "XR": 3}


@pytest.mark.parametrize("modality", ["CR", "CT", "MR", "US", "MG"])
def test_flooded_file_keeps_only_allowed(modality: str) -> None:  # TR-DEID-01, TR-QA-01
    src = _source(modality)
    assert flood(src) >= 200
    out = build_dataset(src, policy_for(modality), _ctx(src))
    allowed = policy_for(modality).allowed_tags
    tags = {int(el.tag) for el in out}
    assert tags <= allowed
    assert not [
        el for el in out if el.tag.is_private or el.VR == "SQ" or el.tag.group & 0xFF00 in (0x5000, 0x6000)
    ]
    assert check_a1(out, "f.dcm") == []
    text = " ".join(str(el.value) for el in out if el.VR not in ("OB", "OW"))
    assert "Sharma" not in text and "Ramesh" not in text and "778812" not in text


def test_other_profile_element_fails_a1() -> None:  # TR-QA-01
    src = _source("CR")
    out = build_dataset(src, policy_for("CR"), _ctx(src))
    out.RepetitionTime = "500"  # MR profile element on an X-ray
    findings = check_a1(out, "f.dcm")
    assert findings and "MR profile" in findings[0].message
    ct = build_dataset(_source("CT"), policy_for("CT"), _ctx(_source("CT")))
    ct.DistanceSourceToDetector = "1000"  # XR profile element on a CT
    assert any("XR profile" in f.message for f in check_a1(ct, "f.dcm"))


def test_a1_catches_private_sequence_overlay() -> None:  # TR-QA-01
    src = _source("CR")
    out = build_dataset(src, policy_for("CR"), _ctx(src))
    out.add_new(0x00291010, "LO", "x")
    out.add_new(0x60000010, "US", 8)
    out.add_new(0x00081115, "SQ", Sequence([Dataset()]))
    del out.ImageComments
    msgs = " | ".join(f.message for f in check_a1(out, "f.dcm"))
    assert (
        "private" in msgs and "overlay" in msgs and "not on the allowlist" in msgs and "ImageComments" in msgs
    )


def test_mr_sequence_name_scrubbed() -> None:  # A01 fixture, TR-DEID-06
    src = _source("MR")
    src.SequenceName = "T2 SHARMA"
    src.RepetitionTime, src.EchoTime = "4000", "90"
    out = build_dataset(src, policy_for("MR"), _ctx(src))
    assert out.SequenceName == "T2 [REDACTED]" and out.RepetitionTime == 4000
    assert check_a1(out, "f.dcm") == []


def test_us_region_item_rebuilt() -> None:  # A01 fixture, TR-DEID-02
    src = _source("US")
    item = Dataset()
    item.RegionSpatialFormat, item.PhysicalDeltaX = 1, 0.01
    item.add_new(0x00291010, "LO", "Ramesh")  # planted private tag inside an item
    item.add_new(0x00081115, "SQ", Sequence([Dataset()]))
    src.SequenceOfUltrasoundRegions = Sequence([item])
    out = build_dataset(src, policy_for("US"), _ctx(src))
    new_item = out.SequenceOfUltrasoundRegions[0]
    assert {e.keyword for e in new_item} == {"RegionSpatialFormat", "PhysicalDeltaX"}
    assert check_a1(out, "f.dcm") == []
    ct = build_dataset(_source("CT"), policy_for("CT"), _ctx(_source("CT")))
    ct.SequenceOfUltrasoundRegions = Sequence([Dataset()])
    assert check_a1(ct, "f.dcm")  # the US exception does not apply to CT


def test_generated_and_identity() -> None:  # TR-DEID-01, TR-DEID-03, TR-DEID-05
    src = _source("CR")
    src.StudyDate = "20260901"
    src.PatientBirthDate, src.PatientAge = "19300101", ""
    out = build_dataset(src, policy_for("CR"), _ctx(src))
    assert out.AccessionNumber == out.StudyID == "S0123456789AB"
    assert out.PatientID == out.PatientName == "P0123456789AB"
    assert out.ImageComments == "S0123456789AB-0002-000001" and out.PatientIdentityRemoved == "YES"
    assert out.StudyDate == "20260822" and out.PatientAge == "090Y"
    assert out.StudyInstanceUID.startswith("2.25.") and out.SOPInstanceUID != src.SOPInstanceUID
    assert out.file_meta.MediaStorageSOPInstanceUID == out.SOPInstanceUID
    assert keyword_for_tag(0x00100030) not in out  # PatientBirthDate never kept


def test_zero_pixel_spacing_dropped() -> None:  # TR-DEID-08 (A6)
    src = _source("CR")
    src.PixelSpacing = [0, 0]
    src.ImagerPixelSpacing = [0.1, 0.1]
    out = build_dataset(src, policy_for("CR"), _ctx(src))
    assert "PixelSpacing" not in out and "ImagerPixelSpacing" in out


def test_size_and_weight_never_kept() -> None:  # TR-DEID-11
    al = load_allowlist()
    keywords = {
        e["keyword"] for e in (*al.core, *al.generated, *(e for p in al.profiles.values() for e in p))
    }
    assert not {"PatientSize", "PatientWeight"} & keywords
    src = _source("CR")
    src.PatientSize, src.PatientWeight = "1.7", "70"
    out = build_dataset(src, policy_for("CR"), _ctx(src))
    assert "PatientSize" not in out and "PatientWeight" not in out
