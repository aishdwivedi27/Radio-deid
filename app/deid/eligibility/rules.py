"""Eligibility screen over ``docs/schemas/exclusion_rules.json`` (SPEC §6.4). TR-ELIG-01..11, TR-COH-05.

Runs before de-identification on the original headers and report text, in memory only. Rules are evaluated
in the file's precedence order; the first match is the reason, the others are listed by rule ID. Matching
is case-insensitive. ``rule_id`` is ``R{order:02d}.{reason}`` (the pinned rules file has no id field).
Image-level rules (order 11) are applied per image during processing, not here. An unknown rule type stops
processing (fail closed).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydicom.dataset import Dataset

from app.deid.dicom.dates import age_from, age_source, age_years
from app.deid.eligibility import evidence as ev
from app.deid.identifiers import parse_da
from app.deid.schemas import load_exclusion_rules
from app.deid.types import DeidError, ScreenContext, ScreenResult

SYNTHETIC_INSTITUTION = "SYNTHETIC-DEMO"
SYNTHETIC_PREFIX = "DEMO-"
ADULT_AGE = 18
Rule = dict[str, Any]


class Study:
    """The screen's view of one study: original header fields and report text (memory only)."""

    def __init__(self, headers: Sequence[Dataset], report_text: str | None, patient_code: str) -> None:
        self.headers = list(headers)
        self.report = report_text
        self.patient_code = patient_code
        self.modalities = {str(d.get("Modality", "") or "").upper() for d in self.headers} - {""}
        self.study_date = next(
            (parse_da(d.get("StudyDate")) for d in self.headers if d.get("StudyDate")), None
        )

    def field_values(self, field: str) -> list[str]:
        if field == "report":
            return [self.report] if self.report else []
        return [str(d.get(field, "") or "") for d in self.headers if d.get(field)]

    def age(self) -> tuple[str, str]:
        for d in self.headers:
            if a := age_from(d):
                return a, age_source(d)
        return "", ""


def is_synthetic(ds: Dataset) -> bool:
    return str(ds.get("InstitutionName", "") or "") == SYNTHETIC_INSTITUTION and str(
        ds.get("PatientID", "") or ""
    ).startswith(SYNTHETIC_PREFIX)


def _kw_regex(kw: str, mode: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(p) for p in kw.split())
    tail = "" if mode == "prefix" else r"(?![A-Za-z0-9])"
    return re.compile(rf"(?<![A-Za-z0-9]){body}{tail}", re.IGNORECASE)


def _keyword_hit(study: Study, rule: Rule) -> str | None:
    for field in rule.get("fields", []):
        for value in study.field_values(field):
            for kw in rule.get("keywords", []):
                if _kw_regex(kw, rule.get("match", "word")).search(value):
                    return ev.keyword(field, kw)
    return None


def _flags(study: Study, ctx: ScreenContext) -> frozenset[str]:
    return ctx.flags.get(study.patient_code, frozenset())


def _mode(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    if ctx.approval_active or all(is_synthetic(d) for d in study.headers):
        return None
    return ev.not_synthetic()


def _date(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    if ctx.archive_start and study.study_date and study.study_date < ctx.archive_start:
        return ev.before_archive_start()
    return None


def _consent(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    if not (ctx.waiver_cutoff and study.study_date and study.study_date > ctx.waiver_cutoff):
        return None
    flags = _flags(study, ctx)
    if "CONSENT" not in flags or "OPT_OUT" in flags:
        return ev.after_cutoff("OPT_OUT" in flags)
    return None


def _flag(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    return ev.flag(rule["flag"]) if rule["flag"] in _flags(study, ctx) else None


def _age(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    age, source = study.age()
    if rule["reason"] == "AGE_UNKNOWN":
        return ev.age_unknown() if not age else None
    years = age_years(age)
    return ev.age(age, source) if years is not None and years < ADULT_AGE else None


def _keywords(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    mods = rule.get("modalities")
    if mods and not study.modalities & set(mods):
        return None
    return _keyword_hit(study, rule)


def _flag_or_keywords(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    return _flag(study, rule, ctx) or _keyword_hit(study, rule)


def _face(study: Study, rule: Rule, ctx: ScreenContext) -> str | None:
    """CT/MR head and neck by keyword; any CBCT; cephalograms (interpretation of the rule's ``also`` text)."""
    mod = ",".join(sorted(study.modalities))
    if study.modalities & set(rule.get("modalities", [])) and (hit := _keyword_hit(study, rule)):
        return ev.face(mod, hit)
    cbct = {**rule, "keywords": ["CBCT"], "match": "word"}  # owner 6 Oct 2026: CBCT keyword only
    if hit := _keyword_hit(study, cbct):
        return ev.face(mod, f"CBCT: {hit}")
    ceph = {**rule, "keywords": ["CEPH", "CEPHALO"], "match": "prefix"}
    if study.modalities & {"DX", "CR", "PX"} and (hit := _keyword_hit(study, ceph)):
        return ev.face(mod, f"cephalogram: {hit}")
    lat = {**rule, "keywords": ["LAT", "LATERAL"], "match": "word"}
    if "PX" in study.modalities and (hit := _keyword_hit(study, lat)):
        return ev.face(mod, f"lateral dental: {hit}")
    return None


CHECKS: dict[str, Callable[[Study, Rule, ScreenContext], str | None]] = {
    "mode": _mode,
    "date": _date,
    "consent": _consent,
    "flag": _flag,
    "age": _age,
    "keywords": _keywords,
    "flag_or_keywords": _flag_or_keywords,
    "keywords_quarantine": _face,
}
IMAGE_LEVEL = "image"


def screen(
    headers: Sequence[Dataset], report_text: str | None, patient_code: str, ctx: ScreenContext
) -> ScreenResult:
    rules = load_exclusion_rules()
    study = Study(headers, report_text, patient_code)
    matches: list[tuple[str, str, str]] = []  # (rule_id, reason, evidence)
    for rule in rules["rules"]:
        if rule["type"] == IMAGE_LEVEL:
            continue
        check = CHECKS.get(rule["type"])
        if check is None:
            raise DeidError("exclusion_rules.json has a rule type this version cannot evaluate")
        if (found := check(study, rule, ctx)) is not None:
            matches.append((f"R{rule['order']:02d}.{rule['reason']}", rule["reason"], found))
    after_cutoff = bool(ctx.waiver_cutoff and study.study_date and study.study_date > ctx.waiver_cutoff)
    basis: Literal["waiver", "consent_flag"] = "consent_flag" if after_cutoff else "waiver"
    version = str(rules["rules_version"])
    if not matches:
        return ScreenResult(excluded=False, consent_basis=basis, rules_version=version)
    rule_id, reason, found = matches[0]
    return ScreenResult(
        excluded=True,
        reason=reason,
        rule_id=rule_id,
        evidence=found,
        other_matches=tuple(m[0] for m in matches[1:]),
        consent_basis=basis,
        rules_version=version,
    )


_ABDOMEN = re.compile(r"(?<![A-Za-z0-9])(?:ABDOMEN|ABDOMINAL|ABD)(?![A-Za-z0-9])", re.IGNORECASE)
_DESCRIPTIVE = ("BodyPartExamined", "StudyDescription", "SeriesDescription", "ProtocolName")
PREGNANCY_AGES = range(18, 56)


def review_flags(headers: Sequence[Dataset]) -> list[str]:
    """Flags that make a reviewer checkbox mandatory (SPEC §6.3; TR-ELIG-03, TR-REV-02).

    PREGNANCY_CHECK: abdominal ultrasound, female, age 18-55 ("No pregnancy visible" must be ticked).
    """
    study = Study(headers, None, "")
    female = any(str(d.get("PatientSex", "")).upper() == "F" for d in headers)
    years = age_years(study.age()[0])
    abdominal = any(_ABDOMEN.search(v) for f in _DESCRIPTIVE for v in study.field_values(f))
    if "US" in study.modalities and female and years in PREGNANCY_AGES and abdominal:
        return ["PREGNANCY_CHECK"]
    return []
