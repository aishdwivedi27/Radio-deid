"""Free-text scrubbing for allowlisted text fields (SPEC §6.0 handling code ``scrub``). TR-DEID-03."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"
MAX_LEN = 64
_VR_MAX = {"SH": 16, "CS": 16, "LO": 64}


def scrub(text: str, rx: re.Pattern[str] | None, vr: str = "LO") -> str:
    """Replace the patient's own name parts, IDs and accession with [REDACTED]; truncate to 64 characters
    (or the VR's own maximum when shorter, e.g. SH = 16)."""
    out = rx.sub(REDACTED, text) if rx else text
    return out[: min(MAX_LEN, _VR_MAX.get(vr, MAX_LEN))]
