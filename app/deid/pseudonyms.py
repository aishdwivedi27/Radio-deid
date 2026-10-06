"""Deterministic de-identification numbers (SPEC §3). TR-DEID-03.

All IDs are HMAC-SHA256 with the centre's key: the same study and key always give the same IDs, and nobody
without the key can recompute them. The data is pseudonymised: the centre keeps the key.
"""

from __future__ import annotations

import hashlib
import hmac
import string

SERIES_DIGITS = 4
INSTANCE_DIGITS = 6
SUFFIXES = string.ascii_lowercase[1:]  # b..z


def hmac_hex(key: bytes, kind: str, value: str) -> str:
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def patient_code(key: bytes, patient_id: str) -> str:
    return "P" + hmac_hex(key, "patient", patient_id)[:12].upper()


def record_id(key: bytes, study_uid: str) -> str:
    return "S" + hmac_hex(key, "study", study_uid)[:12].upper()


def pseudo_uid(key: bytes, original: str) -> str:
    """2.25.<decimal of a 120-bit int>: a valid DICOM UID under 64 characters that keeps links intact."""
    return "2.25." + str(int(hmac_hex(key, "uid", original)[:30], 16))


def shift_days(key: bytes, patient_id: str) -> int:
    """Same shift (1-365 days back) for every study of one patient (SPEC §6.1 A5)."""
    return int(hmac_hex(key, "shift", patient_id)[:8], 16) % 365 + 1


def id_number(value: int | None, digits: int) -> tuple[int, bool]:
    """image_id number: missing → 0; too long → rightmost digits (D-016). Returns (n, truncated)."""
    if value is None or value < 0:
        return 0, False
    limit = 10**digits
    return value % limit, value >= limit


class ImageIdAllocator:
    """Gives each image of one record a unique ``{record_id}-{series:04}-{instance:06}`` ID, adding -b, -c…
    on collision. Callers must offer images in a deterministic order."""

    def __init__(self, rid: str) -> None:
        self.rid = rid
        self.used: set[str] = set()
        self.truncated = 0

    def allocate(self, series: int | None, instance: int | None) -> str:
        s, t1 = id_number(series, SERIES_DIGITS)
        i, t2 = id_number(instance, INSTANCE_DIGITS)
        self.truncated += int(t1 or t2)
        base = f"{self.rid}-{s:0{SERIES_DIGITS}d}-{i:0{INSTANCE_DIGITS}d}"
        candidates = [base, *(f"{base}-{c}" for c in SUFFIXES)]
        for cand in candidates:
            if cand not in self.used:
                self.used.add(cand)
                return cand
        raise ValueError("more than 25 images share one series/instance number")
