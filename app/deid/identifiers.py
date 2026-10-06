"""Identifiers taken from a study's own original headers (SPEC §6.1 A2, §6.2 layer 2). TR-QA-04.

Port of ``reference/deid_prototype/core.known_identifiers`` (29 Sep 2026) with the vendor exception, plus
address parts, digit-only phone numbers and more date-of-birth formats. Matching is case-insensitive and on
token boundaries, so the operator name part "Tech" does not match the word "Technique".
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable

from pydicom.dataset import Dataset

from app.deid.types import Identifiers

MIN_LEN = 3
SYNTHETIC_PREFIX = "DEMO-"
_TITLES = {"dr", "mr", "mrs", "ms", "smt", "shri", "sri", "miss", "kumari", "master", "baby"}
_ID_TAGS = ("PatientID", "AccessionNumber", "OtherPatientIDs", "PatientAddress", "PatientTelephoneNumbers",
            "InstitutionName", "InstitutionAddress", "StationName", "DeviceSerialNumber")
_NAME_TAGS = ("PatientName", "ReferringPhysicianName", "PerformingPhysicianName", "OperatorsName",
              "NameOfPhysiciansReadingStudy", "PatientMotherBirthName", "OtherPatientNames")
_UID_TAGS = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID")
_DATE_TAGS = ("StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "PatientBirthDate")


def norm(value: object) -> str:
    """Lower-case, punctuation-insensitive form used to compare vendor names."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def parse_da(value: object) -> dt.date | None:
    try:
        return dt.datetime.strptime(str(value)[:8], "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def date_forms(d: dt.date) -> set[str]:
    return {d.strftime(f) for f in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y%m%d", "%Y-%m-%d", "%d %b %Y",
                                    "%d %B %Y", "%d/%m/%y")}


def _multi(ds: Dataset, tag: str) -> list[str]:
    value = ds.get(tag, None)
    if value is None:
        return []
    if isinstance(value, (list, tuple)) or type(value).__name__ == "MultiValue":
        return [str(v) for v in value]
    return [str(value)]


def is_vendor_name(ds: Dataset, value: str) -> bool:
    """SPEC §6.1 A2 vendor exception: an institution value equal to Manufacturer or model is not an ID."""
    vendor = {norm(ds.get(k, "")) for k in ("Manufacturer", "ManufacturerModelName")} - {""}
    return norm(value) in vendor


def known_identifiers(ds: Dataset) -> set[str]:
    vals: set[str] = set()
    for tag in _ID_TAGS:
        for v in (x.strip() for x in _multi(ds, tag)):
            if tag.startswith("Institution") and is_vendor_name(ds, v):
                continue
            if len(v) >= MIN_LEN:
                vals.add(v)
            if tag.endswith("Address"):
                vals.update(p.strip() for p in v.split(",") if len(p.strip()) >= 5)
            if tag == "PatientTelephoneNumbers":
                digits = re.sub(r"\D", "", v)
                vals.update({digits, digits[-10:]} if len(digits) >= 10 else set())
    for tag in _NAME_TAGS:
        for v in _multi(ds, tag):
            for part in re.split(r"[\^\s,.]+", v):
                if len(part) >= MIN_LEN and part.lower() not in _TITLES:
                    vals.add(part)
    pid = str(ds.get("PatientID", "") or "")
    if pid.upper().startswith(SYNTHETIC_PREFIX) and len(pid) - len(SYNTHETIC_PREFIX) >= MIN_LEN:
        vals.add(pid[len(SYNTHETIC_PREFIX):])  # the synthetic marker is not part of the planted ID (X9)
    vals.update(u for t in _UID_TAGS if len(u := str(ds.get(t, "") or "")) >= MIN_LEN)
    dob = parse_da(ds.get("PatientBirthDate", ""))
    if dob:
        vals.update(date_forms(dob))
    return vals


def original_dates(ds: Dataset) -> set[str]:
    return {str(ds.get(t)) for t in _DATE_TAGS if parse_da(ds.get(t, ""))}


def collect(datasets: Iterable[Dataset]) -> Identifiers:
    values: set[str] = set()
    dates: set[str] = set()
    for ds in datasets:
        values |= known_identifiers(ds)
        dates |= original_dates(ds)
    return Identifiers(values=frozenset(values), dates=frozenset(dates))


def identifier_regex(values: Iterable[str]) -> re.Pattern[str] | None:
    """One case-insensitive pattern for all identifiers, longest first, on alphanumeric token boundaries."""
    items = sorted({v for v in values if len(v) >= MIN_LEN}, key=len, reverse=True)
    if not items:
        return None
    alts = "|".join(re.escape(v) for v in items)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])", re.IGNORECASE)
