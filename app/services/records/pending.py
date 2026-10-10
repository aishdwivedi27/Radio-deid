"""Pending records for the review queue: read-only, from ``work/pending`` (SPEC §4, §6.3). TR-REV-01,
TR-REV-06.

The detail shows the reviewer exactly what would be appended: the record row and the new image rows as JSON
and as CSV, built with the same column lists and flattening rules as the appenders. Fields filled only at
approval (reviewer, decision, times, finding category, cohort reference) are listed. Images are rendered from
the de-identified pending DICOM files only.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from app.auth.errors import Actor, not_found
from app.deid.record.columns import IMAGE_COLUMNS, RECORD_COLUMNS
from app.deid.record.flatten import image_csv, record_csv
from app.deid.record.preview import render_png
from app.services.auth import access
from app.services.context import ServiceContext
from app.store.models.records import Record, RecordVersion
from app.store.output.csv_writer import encode_row
from app.store.repos import records as recs

PENDING_STATES = ("awaiting_review", "auto_qa_failed")
FILLED_AT_APPROVAL = (
    "reviewer_id",
    "decision",
    "decided_at",
    "finalised_at",
    "finding_category",
    "cohort_ref",
)
RECORD_ID = re.compile(r"^S[0-9A-F]{12}$")
IMAGE_ID = re.compile(r"^S[0-9A-F]{12}-\d{4}-\d{6}(-[a-z])?$")


def _latest_pending(s: Any, record_id: str) -> RecordVersion | None:
    if not RECORD_ID.match(record_id or ""):
        return None
    record = recs.get_record(s, record_id)
    if record is None:
        return None
    ver = recs.get_version(s, record_id, record.latest_version)
    if ver is None or ver.finalised_at is not None or ver.state not in PENDING_STATES:
        return None
    return ver


def list_pending(
    ctx: ServiceContext, actor: Actor, state: str | None = None, job_id: str | None = None
) -> list[dict[str, Any]]:
    access.check(ctx, actor, "pending.view", "pending.list")
    out = []
    with ctx.db.session() as s:
        q = (
            select(RecordVersion)
            .join(Record, Record.record_id == RecordVersion.record_id)
            .where(RecordVersion.version == Record.latest_version, RecordVersion.finalised_at.is_(None))
            .where(RecordVersion.state.in_(PENDING_STATES))
            .order_by(RecordVersion.created_at, RecordVersion.record_id)
        )
        for ver in s.scalars(q):
            if (state and ver.state != state) or (job_id and ver.job_id != job_id):
                continue
            row = json.loads(ver.row_json)
            out.append(
                {
                    "record_id": ver.record_id,
                    "version": ver.version,
                    "state": ver.state,
                    "job_id": ver.job_id,
                    "modalities": row.get("modalities"),
                    "body_part": row.get("body_part"),
                    "n_images": row.get("n_images"),
                    "report_present": bool((row.get("report") or {}).get("present")),
                    "review_flags": json.loads(ver.review_flags_json or "[]"),
                    "findings_count": len(json.loads(ver.findings_json or "[]")),
                    "created_at": ver.created_at,
                }  # fmt: skip
            )
    return out


def _csv_line(values: list[str]) -> str:
    return encode_row(values).decode("utf-8")


def append_preview(row: dict[str, Any], image_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The record row and new image rows exactly as they would be appended, as JSON and CSV."""
    return {
        "record": {
            "json": row,
            "csv_header": _csv_line(list(RECORD_COLUMNS)),
            "csv_row": _csv_line(record_csv(row)),
        },
        "images": {
            "json": image_rows,
            "csv_header": _csv_line(list(IMAGE_COLUMNS)),
            "csv_rows": [_csv_line(image_csv(r)) for r in image_rows],
        },
        "filled_at_approval": list(FILLED_AT_APPROVAL),
    }


def get_pending(ctx: ServiceContext, actor: Actor, record_id: str) -> dict[str, Any]:
    access.check(ctx, actor, "pending.view", "pending.get")
    with ctx.db.session() as s:
        ver = _latest_pending(s, record_id)
        if ver is None:
            raise not_found("No such pending record.")
        row = json.loads(ver.row_json)
        image_rows = [json.loads(i.row_json) for i in recs.images(s, record_id, recs.PENDING)]
        flags, findings = json.loads(ver.review_flags_json or "[]"), json.loads(ver.findings_json or "[]")
        version, state, job_id = ver.version, ver.state, ver.job_id
    report = row.get("report") or {}
    folder = ctx.paths.pending_root / record_id
    report_file = folder / f"{record_id}_report.txt"
    text = (
        report_file.read_text(encoding="utf-8") if report.get("present") and report_file.is_file() else None
    )
    banners = ([] if report.get("present") else ["NO_REPORT"]) + (
        ["OCR_REPORT"] if report.get("extraction") == "ocr" else []
    )
    by_id = {r["image_id"]: r for r in image_rows}
    base = f"/api/pending/{record_id}"
    images = [
        {
            "image_id": i,
            "url": f"{base}/image/{i}.png",
            "new": i in by_id,
            "modality": by_id.get(i, {}).get("modality"),
            "ocr_regions": by_id.get(i, {}).get("ocr_regions"),
        }  # fmt: skip
        for i in row.get("image_ids") or []
    ]
    return {
        "record_id": record_id,
        "version": version,
        "state": state,
        "job_id": job_id,
        "review_flags": flags,
        "findings": findings,
        "banners": banners,
        "preview_url": f"{base}/preview.png",
        "images": images,
        "report": {
            "present": bool(report.get("present")),
            "text": text,
            "source_format": report.get("source_format"),
            "extraction": report.get("extraction"),
        },  # fmt: skip
        "append_preview": append_preview(row, image_rows),
    }


def preview_png(ctx: ServiceContext, actor: Actor, record_id: str) -> bytes:
    access.check(ctx, actor, "pending.view", "pending.preview")
    with ctx.db.session() as s:
        if _latest_pending(s, record_id) is None:
            raise not_found("No such pending record.")
    path = ctx.paths.pending_root / record_id / f"{record_id}_preview.png"
    if not path.is_file():
        raise not_found("No preview.")
    return path.read_bytes()


def image_png(ctx: ServiceContext, actor: Actor, record_id: str, image_id: str) -> bytes:
    access.check(ctx, actor, "pending.view", "pending.image")
    if not IMAGE_ID.match(image_id or "") or not image_id.startswith(f"{record_id}-"):
        raise not_found("No such image.")
    with ctx.db.session() as s:
        ver = _latest_pending(s, record_id)
        ids = set(json.loads(ver.row_json).get("image_ids") or []) if ver else set()
    if image_id not in ids:
        raise not_found("No such image.")
    path = ctx.paths.pending_root / record_id / f"{image_id}.dcm"
    if not path.is_file():
        raise not_found("No such image.")
    return render_png(path)
