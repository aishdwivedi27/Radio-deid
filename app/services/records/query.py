"""Read side: Records list, record detail, JSON download (SPEC §5.1 "Download JSON", §5.4). TR-WDR-03.

Only finalised data is returned here; pending records belong to the review queue (Phase 4/5).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from app.services.context import ServiceContext
from app.store.repos import records as recs

EXPORT_FILES = {"records": "records.jsonl", "images": "images.jsonl", "withdrawals": "withdrawals.jsonl"}


@dataclass(frozen=True)
class RecordFilters:
    modality: str | None = None
    body_part: str | None = None
    date_from: dt.date | None = None  # shifted study date
    date_to: dt.date | None = None
    reviewer: str | None = None
    has_report: bool | None = None
    finding_category: str | None = None
    include_withdrawn: bool = False


def _matches(row: dict[str, Any], f: RecordFilters) -> bool:
    study = row.get("study_date") or ""
    date = dt.date.fromisoformat(study) if len(study) == 10 else None
    checks = (
        f.modality is None or f.modality.upper() in row["modalities"],
        f.body_part is None or f.body_part.upper() == (row.get("body_part") or "").upper(),
        f.date_from is None or (date is not None and date >= f.date_from),
        f.date_to is None or (date is not None and date <= f.date_to),
        f.reviewer is None or f.reviewer == row["qa"]["reviewer_id"],
        f.has_report is None or f.has_report == bool(row["report"]["present"]),
        f.finding_category is None or f.finding_category == row.get("finding_category"),
    )
    return all(checks)


def _list_item(row: dict[str, Any], state: str) -> dict[str, Any]:
    rid = row["record_id"]
    return {
        "preview": f"records/{rid}/{rid}_preview.png",
        "record_id": rid,
        "patient_code": row["patient_code"],
        "modalities": row["modalities"],
        "body_part": row.get("body_part", ""),
        "n_images": row["n_images"],
        "report_present": row["report"]["present"],
        "redactions": sum((row["report"].get("redactions") or {}).values()),
        "text_masked": row.get("ocr_regions_masked", 0),
        "finding_category": row.get("finding_category"),
        "version": row["record_version"],
        "finalised_at": row["finalised_at"],
        "reviewer_id": row["qa"]["reviewer_id"],
        "withdrawn": state == "withdrawn",
    }


def list_records(ctx: ServiceContext, filters: RecordFilters | None = None) -> list[dict[str, Any]]:
    """The latest finalised version of each record (withdrawn ones only on request)."""
    f = filters or RecordFilters()
    out = []
    with ctx.db.session() as s:
        for record in recs.finalised_records(s):
            if record.state == "withdrawn" and not f.include_withdrawn:
                continue
            ver = recs.get_version(s, record.record_id, record.finalised_version or 0)
            assert ver is not None
            row = json.loads(ver.row_json)
            if _matches(row, f):
                out.append(_list_item(row, record.state))
    return out


def release_candidates(ctx: ServiceContext) -> list[tuple[str, int]]:
    """(record_id, version) that a release may consider: finalised and not withdrawn (TR-WDR-03)."""
    return [(r["record_id"], r["version"]) for r in list_records(ctx)]


def get_record(ctx: ServiceContext, record_id: str) -> dict[str, Any] | None:
    with ctx.db.session() as s:
        record = recs.get_record(s, record_id)
        if record is None:
            return None
        versions = [
            {
                "version": v.version,
                "state": v.state,
                "finalised_at": v.finalised_at,
                "reviewer_id": v.reviewer_id,
                "finding_category": v.finding_category,
                "row": json.loads(v.row_json),
            }
            for v in recs.versions(s, record_id)
        ]
        images = [json.loads(i.row_json) for i in recs.finalised_images(s, record_id)]
        return {
            "record_id": record.record_id,
            "patient_code": record.patient_code,
            "state": record.state,
            "finalised_version": record.finalised_version,
            "versions": versions,
            "images": images,
        }


def export_json_array(ctx: ServiceContext, kind: str) -> Iterator[bytes]:
    """Stream ``[line,line,…]`` from the JSONL file without loading it whole."""
    if kind not in EXPORT_FILES:
        raise ValueError("kind must be records, images or withdrawals")
    path = ctx.paths.output_file(EXPORT_FILES[kind])
    yield b"["
    if path.exists():
        first = True
        with path.open("rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # an unacknowledged fragment (reconcile removes it)
                line = raw.rstrip(b"\n")
                if line:
                    yield line if first else b"," + line
                    first = False
    yield b"]"
