"""Eligibility screen (SPEC §6.4, §4.1; fixtures T1-T3, T5-T8). TR-ELIG-01..11, TR-COH-05, TR-DEID-04."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.deid.eligibility.rules import review_flags, screen
from app.deid.pseudonyms import patient_code
from app.deid.types import DeidError, DeidSettings, ScreenContext
from tests.deid.helpers import make_ds

KEY = b"k" * 32
PID = "DEMO-UHID-7001"
PCODE = patient_code(KEY, PID)
NO_CTX = ScreenContext()


def _screen(report: str | None = None, ctx: ScreenContext = NO_CTX, **kw: Any):  # type: ignore[no-untyped-def]
    kw.setdefault("patient_id", PID)
    return screen([make_ds(**kw)], report, PCODE, ctx)


def test_eligible_adult() -> None:  # TR-ELIG-01
    r = _screen("Chest: normal.", BodyPartExamined="CHEST")
    assert not r.excluded and r.rules_version and r.consent_basis == "waiver"


def test_t1_under_18() -> None:  # T1, TR-ELIG-01
    r = _screen(PatientAge="016Y")
    assert (
        r.reason == "AGE_UNDER_18"
        and r.rule_id == "R06.AGE_UNDER_18"
        and r.evidence == "age 016Y from PatientAge"
    )


def test_t2_age_unknown() -> None:  # T2, TR-ELIG-01
    r = _screen(PatientAge=None)
    assert r.reason == "AGE_UNKNOWN" and r.evidence.startswith("age unknown")


def test_age_from_birth_date() -> None:  # TR-ELIG-01
    r = _screen(PatientAge=None, PatientBirthDate="20100101", StudyDate="20260901")
    assert r.reason == "AGE_UNDER_18" and r.evidence == "age 016Y from PatientBirthDate"


def test_t3_pelvic_us_report_lmp() -> None:  # T3, TR-ELIG-02
    r = _screen("USG PELVIS. LMP 12 days ago.", modality="US", BodyPartExamined="PELVIS")
    assert r.reason == "OB_PELVIC_US" and r.evidence == "BodyPartExamined contains PELVIS"
    r = _screen("Uterus normal. LMP noted.", modality="US", BodyPartExamined="ABDOMEN")
    assert r.reason == "OB_PELVIC_US" and r.evidence == "report contains LMP"
    assert not _screen("Uterus normal.", modality="CT", BodyPartExamined="ABDOMEN").excluded  # US only


def test_t5_medico_legal() -> None:  # T5, TR-ELIG-04
    r = _screen("MLC No. 445/26. Fracture of radius.")
    assert r.reason == "MEDICO_LEGAL" and r.evidence == "report contains MLC"
    flagged = ScreenContext(flags={PCODE: frozenset({"MEDICO_LEGAL"})})
    assert _screen(ctx=flagged).reason == "MEDICO_LEGAL"


def test_t6_staff_vip_flag_by_patient_code() -> None:  # T6, TR-ELIG-05, TR-ELIG-05
    ctx = ScreenContext(flags={PCODE: frozenset({"STAFF_VIP"})})
    r = _screen(ctx=ctx)
    assert r.reason == "STAFF_VIP" and PID not in repr(ctx)


def test_t7_sensitive_referral() -> None:  # T7, TR-ELIG-06
    r = _screen("Clinical history: RVD positive.")
    assert r.reason == "SENSITIVE_REFERRAL" and r.evidence == "report contains RVD"


@pytest.mark.parametrize(
    ("kw", "evidence_part"),
    [
        ({"modality": "CT", "BodyPartExamined": "HEAD"}, "BodyPartExamined contains HEAD"),
        ({"modality": "CT", "StudyDescription": "CBCT MAXILLA"}, "contains"),
        ({"modality": "DX", "StudyDescription": "LATERAL CEPHALOGRAM"}, "cephalogram"),
        ({"modality": "PX", "StudyDescription": "LAT SKULL VIEW"}, "lateral dental"),
    ],
)
def test_t8_face_bearing(kw: dict[str, str], evidence_part: str) -> None:  # T8, TR-ELIG-07, TR-DEID-12
    r = _screen(**kw)
    assert r.reason == "FACE_BEARING" and evidence_part in r.evidence


def test_pre_approval_refuses_real_data() -> None:  # TR-COH-05
    r = _screen(InstitutionName="City Hospital")
    assert r.reason == "PRE_APPROVAL_REAL_DATA" and "City" not in r.evidence
    assert _screen(patient_id="UHID-1").reason == "PRE_APPROVAL_REAL_DATA"
    assert not _screen(InstitutionName="City Hospital", ctx=ScreenContext(approval_active=True)).excluded


def test_dates_and_consent() -> None:  # TR-ELIG-09, TR-COH-03
    ctx = ScreenContext(archive_start=dt.date(2026, 1, 1), waiver_cutoff=dt.date(2026, 6, 30))
    assert _screen(ctx=ctx, StudyDate="20251231").reason == "OUT_OF_DATE_RANGE"
    assert _screen(ctx=ctx, StudyDate="20260901").reason == "NO_CONSENT_AFTER_CUTOFF"
    consent = ScreenContext(waiver_cutoff=dt.date(2026, 6, 30), flags={PCODE: frozenset({"CONSENT"})})
    r = _screen(ctx=consent, StudyDate="20260901")
    assert not r.excluded and r.consent_basis == "consent_flag"


def test_precedence_and_other_matches() -> None:  # TR-ELIG-10
    r = _screen("MLC case, RVD positive", PatientAge="015Y")
    assert r.reason == "AGE_UNDER_18"
    assert r.other_matches == ("R08.MEDICO_LEGAL", "R09.SENSITIVE_REFERRAL")


def test_image_only_report_rules_cannot_fire() -> None:  # TR-RPT-05, TR-ELIG-02
    assert not _screen(None, modality="US", BodyPartExamined="ABDOMEN").excluded


def test_evidence_never_holds_identifiers() -> None:  # TR-ELIG-11
    cases = [
        _screen(PatientAge="016Y", name="Kumar^Ravi", PatientBirthDate="20100101"),
        _screen(PatientAge=None, name="Kumar^Ravi"),
        _screen("MLC No. 445/26 Ravi Kumar", name="Kumar^Ravi"),
        _screen(InstitutionName="City Hospital", name="Kumar^Ravi"),
    ]
    for r in cases:
        text = r.evidence + " ".join(r.other_matches)
        for leak in ("Ravi", "Kumar", "7001", "20100101", "445/26", "City"):
            assert leak not in text


def test_date_mode_locked_under_approval() -> None:  # TR-DEID-04
    approved = ScreenContext(approval_active=True)
    with pytest.raises(DeidError):
        DeidSettings(date_mode="year_only", screen=approved)
    assert (
        DeidSettings(date_mode="year_only", screen=approved, ec_amendment_ref="EC-AMD-1").date_mode
        == "year_only"
    )
    assert DeidSettings(date_mode="remove").date_mode == "remove"  # pre-approval (synthetic only)


def test_t4_pregnancy_check_flag() -> None:  # T4 (library part), TR-ELIG-03
    def flags(**kw: Any) -> list[str]:
        return review_flags([make_ds(**kw)])

    assert flags(modality="US", PatientSex="F", PatientAge="030Y", BodyPartExamined="ABDOMEN") == [
        "PREGNANCY_CHECK"
    ]
    assert flags(modality="US", PatientSex="M", PatientAge="030Y", BodyPartExamined="ABDOMEN") == []
    assert flags(modality="US", PatientSex="F", PatientAge="060Y", BodyPartExamined="ABDOMEN") == []
    assert flags(modality="CT", PatientSex="F", PatientAge="030Y", BodyPartExamined="ABDOMEN") == []
