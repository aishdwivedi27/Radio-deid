"""JSON row → CSV values with the SPEC §5.2 flattening rules. TR-DEID-10.

Lists are joined with ``|``; maps become ``KEY:n|KEY:n``; booleans ``true``/``false``; ``None`` is empty;
line breaks inside a value become a space, so one CSV row is always one line (the tail scan relies on it).
The CSV row is always built from the same dict as the JSON line, so the two cannot disagree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.deid.record.columns import IMAGE_COLUMNS, RECORD_COLUMNS, WITHDRAWAL_COLUMNS

_BREAKS = re.compile(r"[\r\n]+")


def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return "|".join(cell(v) for v in value)
    if isinstance(value, Mapping):
        return "|".join(f"{k}:{cell(v)}" for k, v in value.items())
    return _BREAKS.sub(" ", str(value))


def _overall(checks: Mapping[str, str]) -> str:
    return "fail" if any(v == "fail" for v in checks.values()) else "pass"


def record_values(row: Mapping[str, Any]) -> dict[str, Any]:
    report, qa = row.get("report") or {}, row.get("qa") or {}
    excluded = row.get("images_excluded") or []
    redactions = report.get("redactions") or {}
    flat = {k: row.get(k) for k in RECORD_COLUMNS if k in row}
    flat.update(
        {
            "report_present": bool(report.get("present")),
            "report_file": report.get("file"),
            "report_sha256": report.get("sha256"),
            "report_source_format": report.get("source_format"),
            "report_extraction": report.get("extraction"),
            "report_redactions_total": sum(redactions.values()),
            "report_redactions": redactions,
            "images_excluded_total": sum(int(e["count"]) for e in excluded),
            "images_excluded": {e["reason"]: e["count"] for e in excluded},
            "qa_auto": _overall(qa.get("auto_checks") or {}),
            "qa_consistency": _overall(qa.get("consistency_checks") or {}),
            "reviewer_id": qa.get("reviewer_id"),
            "decision": qa.get("decision"),
            "decided_at": qa.get("decided_at"),
        }
    )
    return flat


def record_csv(row: Mapping[str, Any]) -> list[str]:
    flat = record_values(row)
    return [cell(flat.get(c)) for c in RECORD_COLUMNS]


def image_csv(row: Mapping[str, Any]) -> list[str]:
    return [cell(row.get(c)) for c in IMAGE_COLUMNS]


def withdrawal_csv(row: Mapping[str, Any]) -> list[str]:
    return [cell(row.get(c)) for c in WITHDRAWAL_COLUMNS]
