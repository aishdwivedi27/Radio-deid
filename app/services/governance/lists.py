"""Patient-list import (SPEC §12.2). TR-LIST-01..05, CLAUDE.md rule 13.

The Custodian uploads a CSV keyed by UHID (column ``uhid``; CONSENT lists add ``consent`` Yes/No and an
optional ``consent_date`` YYYY-MM-DD). The body is parsed in memory only: nothing is written to disk, so
there is no staging copy to wipe. Each UHID becomes ``patient_code`` = HMAC("patient:" + UHID) at once and
the raw values are dropped; errors name row numbers, never values. Lists are append-only (a new list
version per import). OPT_OUT and STAFF_VIP imports then withdraw matching finalised records.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Sequence

from app.auth.errors import Actor, AppError, bad_request
from app.deid import pseudonyms as ps
from app.deid.keys import load_or_create_key
from app.services.auth import access
from app.services.context import ServiceContext
from app.services.records.withdraw import LIST_TYPES, FlagResult, add_flags

MAX_BYTES = 5 * 1024 * 1024
_ID_COLUMNS = ("uhid", "patient_id", "patientid")

Entry = tuple[str, str | None, str | None]


def _header(fields: Sequence[str] | None) -> dict[str, str]:
    return {f.strip().lower(): f for f in (fields or [])}


def parse_csv(body: bytes, list_type: str, key: bytes) -> list[Entry]:
    """(patient_code, consent, consent_date) per distinct UHID. The raw IDs never leave this function."""
    if len(body) > MAX_BYTES:
        raise bad_request("the list file is larger than 5 MB", "list_too_large")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise bad_request("the list file must be UTF-8 CSV", "list_format") from None
    reader = csv.DictReader(io.StringIO(text))
    cols = _header(reader.fieldnames)
    id_col = next((cols[c] for c in _ID_COLUMNS if c in cols), None)
    if id_col is None:
        raise bad_request("the list needs a 'uhid' column", "list_format")
    consent_col, date_col = cols.get("consent"), cols.get("consent_date")
    if list_type == "CONSENT" and consent_col is None:
        raise bad_request("a CONSENT list needs a 'consent' column (Yes/No)", "list_format")
    out: dict[str, Entry] = {}
    for n, row in enumerate(reader, start=2):  # row 1 is the header
        uhid = (row.get(id_col) or "").strip()
        if not uhid:
            raise bad_request(f"row {n}: the uhid is empty", "list_format")
        consent = date = None
        if list_type == "CONSENT":
            consent = (row.get(consent_col or "") or "").strip().capitalize()
            if consent not in ("Yes", "No"):
                raise bad_request(f"row {n}: consent must be Yes or No", "list_format")
            date = (row.get(date_col or "") or "").strip() or None
            if date is not None:
                try:
                    dt.date.fromisoformat(date)
                except ValueError:
                    raise bad_request(f"row {n}: consent_date must be YYYY-MM-DD", "list_format") from None
        code = ps.patient_code(key, uhid)
        out[code] = (code, consent, date)
    if not out:
        raise bad_request("the list has no rows", "list_format")
    return list(out.values())


def import_list(ctx: ServiceContext, actor: Actor, list_type: str, body: bytes) -> FlagResult:
    access.check(ctx, actor, "lists.import", "lists.import")
    if list_type not in LIST_TYPES:
        raise bad_request("list type must be OPT_OUT, STAFF_VIP, MEDICO_LEGAL or CONSENT", "list_type")
    if not ctx.paths.key_path.exists():
        raise AppError(409, "no_key", "The Custodian must create the key first.")
    entries = parse_csv(body, list_type, load_or_create_key(ctx.paths.key_path))
    return add_flags(ctx, list_type, entries, actor.user_id)
