"""Non-identifying evidence for ``exclusions.csv`` (SPEC §6.4, TR-ELIG-11).

Evidence says what triggered a rule, using only rule keywords, field names, modality and computed age.
It never contains names, IDs, dates of birth or any other header value.
"""

from __future__ import annotations


def age(value: str, source: str) -> str:
    return f"age {value} from {source}"


def age_unknown() -> str:
    return "age unknown: no PatientAge or PatientBirthDate"


def keyword(field: str, kw: str) -> str:
    return f"{field} contains {kw}"


def flag(name: str) -> str:
    return f"patient flag {name}"


def not_synthetic() -> str:
    return "not a synthetic file (InstitutionName/PatientID markers) while in pre-approval mode"


def before_archive_start() -> str:
    return "StudyDate before the archive start date"


def after_cutoff(opt_out: bool) -> str:
    return (
        "StudyDate after the cut-off with an OPT_OUT flag"
        if opt_out
        else "StudyDate after the cut-off, no CONSENT flag"
    )


def face(modality: str, what: str) -> str:
    return f"{what} (Modality {modality})"
