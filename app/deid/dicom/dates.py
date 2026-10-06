"""Dates and age (SPEC §6.1 A5, §5.2 age_band). TR-DEID-04, TR-DEID-10.

Dates are shifted back by the same 1-365 days for every study of a patient (``pseudonyms.shift_days``).
While an ethics approval is active the mode is locked to ``shift`` (enforced in ``DeidSettings``).
"""

from __future__ import annotations

import datetime as dt
import re

from pydicom.dataset import Dataset

from app.deid.identifiers import parse_da

_AS = re.compile(r"(\d{3})([DWMY])")


def shift_da(value: object, days: int, mode: str) -> str | None:
    """New DA value for a source DA value, or None when the element must be omitted."""
    d = parse_da(value)
    if d is None or mode == "remove":
        return None
    if mode == "year_only":
        return f"{d.year}0101"
    return (d - dt.timedelta(days=days)).strftime("%Y%m%d")


def iso_date(da: str | None, mode: str) -> str:
    """Record-row form of an output DA: YYYY-MM-DD (shift), YYYY (year_only), '' (remove / missing)."""
    d = parse_da(da) if da else None
    if d is None or mode == "remove":
        return ""
    return str(d.year) if mode == "year_only" else d.isoformat()


def _valid_as(ds: Dataset) -> str:
    age = str(ds.get("PatientAge", "") or "").strip().upper()
    return age if _AS.fullmatch(age) else ""


def age_from(ds: Dataset) -> str:
    """PatientAge if well formed, else computed from PatientBirthDate at StudyDate; '' when unknown."""
    if age := _valid_as(ds):
        return age
    dob, sd = parse_da(ds.get("PatientBirthDate", "")), parse_da(ds.get("StudyDate", ""))
    if dob and sd and sd >= dob:
        yrs = sd.year - dob.year - ((sd.month, sd.day) < (dob.month, dob.day))
        return f"{yrs:03d}Y"
    return ""


def age_source(ds: Dataset) -> str:
    if _valid_as(ds):
        return "PatientAge"
    return "PatientBirthDate" if age_from(ds) else ""


def age_years(age: str) -> int | None:
    m = _AS.fullmatch(age or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n if unit == "Y" else (n // 12 if unit == "M" else 0)


def cap_age(age: str, cap: int) -> str:
    years = age_years(age)
    return f"{cap:03d}Y" if years is not None and years >= cap else age


def age_band(age: str) -> str:
    """5-year band from the capped age: 00-04 … 85-89, 90+ (SPEC §5.2)."""
    years = age_years(age)
    if years is None:
        return ""
    if years >= 90:
        return "90+"
    low = years // 5 * 5
    return f"{low:02d}-{low + 4:02d}"
