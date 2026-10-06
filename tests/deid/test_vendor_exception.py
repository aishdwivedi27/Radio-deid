"""T27: a vendor name in InstitutionName equal to Manufacturer is not an identifier (SPEC §6.1 A2 v0.4).

TR-QA-04. Synthetic data; approval_active is set only so this non-marker InstitutionName passes the screen.
"""

from __future__ import annotations

from pathlib import Path

import pydicom
from pydicom.dataset import Dataset

from app.deid.identifiers import known_identifiers
from app.deid.index import index_input
from app.deid.pipeline import process_study
from app.deid.types import DeidSettings, ScreenContext
from tests.deid.helpers import write_study

VENDOR = "VATECH Co., Ltd."


def test_known_identifiers_vendor_rule() -> None:  # T27, TR-QA-04
    ds = Dataset()
    ds.PatientName, ds.InstitutionName = "DEMO^PATIENT", VENDOR
    ds.Manufacturer, ds.ManufacturerModelName = VENDOR, "PHT-75CHS"
    ids = known_identifiers(ds)
    assert VENDOR not in ids and {"DEMO", "PATIENT"} <= ids
    ds.InstitutionName = "vatech co ltd"  # case and punctuation ignored
    assert "vatech co ltd" not in known_identifiers(ds)
    ds.InstitutionName = "Sunrise Diagnostics"
    assert "Sunrise Diagnostics" in known_identifiers(ds)


def test_vendor_kept_and_no_a2_finding(tmp_path: Path, key: bytes) -> None:  # T27, TR-QA-04
    write_study(
        tmp_path / "in",
        modality="DX",
        InstitutionName=VENDOR,
        Manufacturer=VENDOR,
        ManufacturerModelName="PHT-75CHS",
        StudyDescription="PANORAMIC",
    )
    group = index_input(tmp_path / "in").studies[0]
    settings = DeidSettings(screen=ScreenContext(approval_active=True))
    rec = process_study(group, None, key, settings, tmp_path / "pending")
    assert rec.status == "awaiting_review", [str(f) for f in rec.findings]
    assert rec.folder is not None
    ds = pydicom.dcmread(next(rec.folder.glob("*.dcm")))
    assert ds.Manufacturer == VENDOR and ds.ManufacturerModelName == "PHT-75CHS"
    assert rec.record_row is not None and rec.record_row["manufacturer"] == VENDOR


def test_real_institution_still_caught(tmp_path: Path, key: bytes) -> None:  # T27, TR-QA-04
    write_study(
        tmp_path / "in",
        modality="DX",
        InstitutionName="Sunrise Diagnostics",
        Manufacturer=VENDOR,
        StudyDescription="XR CHEST SUNRISE DIAGNOSTICS",
    )
    group = index_input(tmp_path / "in").studies[0]
    rec = process_study(
        group, None, key, DeidSettings(screen=ScreenContext(approval_active=True)), tmp_path / "p"
    )
    assert rec.folder is not None
    ds = pydicom.dcmread(next(rec.folder.glob("*.dcm")))
    assert "SUNRISE" not in str(ds.StudyDescription).upper() and "InstitutionName" not in ds
