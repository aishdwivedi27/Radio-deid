"""Plain data passed in and out of the de-identification core (SPEC §3, §6).

Fields that hold original (identifying) values are excluded from ``repr`` so that an exception message or a
log line that prints one of these objects cannot echo patient data (CLAUDE.md rule 2).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

DateMode = Literal["shift", "year_only", "remove"]
Status = Literal["awaiting_review", "auto_qa_failed", "excluded"]


class DeidError(ValueError):
    """Invalid input or settings. Messages never contain patient data."""


@dataclass(frozen=True)
class SourceFile:
    path: Path = field(repr=False)
    sop_uid: str = field(repr=False)
    series_number: int | None = None
    instance_number: int | None = None


@dataclass
class StudyGroup:
    """One study found in the input (header values in memory only; never logged or written)."""

    study_uid: str = field(repr=False)
    patient_id: str = field(repr=False)
    accession: str = field(repr=False)
    files: list[SourceFile] = field(default_factory=list, repr=False)
    folders: set[Path] = field(default_factory=set, repr=False)

    @property
    def n_files(self) -> int:
        return len(self.files)


@dataclass
class InputIndex:
    studies: list[StudyGroup]
    reports: list[Path] = field(repr=False)
    skipped: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class ExtractedReport:
    text: str = field(repr=False)
    source_format: Literal["txt", "docx", "pdf"]
    extraction: Literal["native", "ocr"]
    pages_ocr: int = 0


@dataclass(frozen=True)
class ScreenContext:
    """What the eligibility screen needs besides the study (SPEC §6.4, §12). Patient flags are keyed by
    patient_code; raw IDs are never held here."""

    approval_active: bool = False
    archive_start: dt.date | None = None
    waiver_cutoff: dt.date | None = None
    flags: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreenResult:
    excluded: bool
    reason: str | None = None
    rule_id: str | None = None
    evidence: str = ""
    other_matches: tuple[str, ...] = ()
    consent_basis: Literal["waiver", "consent_flag"] = "waiver"
    rules_version: str = ""


@dataclass(frozen=True)
class DeidSettings:
    job_id: str = "J-LOCAL"
    date_mode: DateMode = "shift"
    ocr_enabled: bool = True
    ner_enabled: bool = True
    age_cap_years: int = 90
    ec_amendment_ref: str | None = None
    screen: ScreenContext = field(default_factory=ScreenContext)

    def __post_init__(self) -> None:
        if self.date_mode not in ("shift", "year_only", "remove"):
            raise DeidError("date_mode must be shift, year_only or remove")
        # SPEC §6.1 A5 / TR-DEID-04: locked to shift while an approval is active, unless an EC amendment.
        if self.screen.approval_active and self.date_mode != "shift" and not self.ec_amendment_ref:
            raise DeidError("date mode is locked to 'shift' while an ethics approval is active")


@dataclass(frozen=True)
class Identifiers:
    """Original values that must never appear in any output (A2), plus original dates (A5)."""

    values: frozenset[str] = field(repr=False)
    dates: frozenset[str] = field(default_factory=frozenset, repr=False)


@dataclass(frozen=True)
class Finding:
    check: str
    message: str
    file: str = ""

    def __str__(self) -> str:
        return f"{self.check} {self.file}: {self.message}" if self.file else f"{self.check}: {self.message}"


@dataclass
class PendingRecord:
    status: Status
    record_id: str = ""
    patient_code: str = ""
    study_key: str = ""  # full HMAC of the original StudyInstanceUID (exclusions.csv, C7)
    folder: Path | None = None
    record_row: dict[str, Any] | None = None
    image_rows: list[dict[str, Any]] = field(default_factory=list)
    excluded_images: Counter[str] = field(default_factory=Counter)
    redactions: dict[str, int] = field(default_factory=dict)
    ocr_regions: int = 0
    review_flags: list[str] = field(default_factory=list)
    notes: Counter[str] = field(default_factory=Counter)
    findings: list[Finding] = field(default_factory=list)
    screen: ScreenResult | None = None
    report_matched: bool = False
