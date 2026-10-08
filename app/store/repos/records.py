"""Records, versions and images (SPEC §4, §5). TR-REL-NF-01.

A version is finalised when ``finalised_at`` is set; it stays finalised even if the record is later
withdrawn (its line in records.jsonl is never rewritten). Finalised image rows are never changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.store.models.records import Image, Record, RecordVersion

FINALISED = "finalised"
PENDING = "pending"


def get_record(s: Session, record_id: str) -> Record | None:
    return s.get(Record, record_id)


def get_version(s: Session, record_id: str, version: int) -> RecordVersion | None:
    return s.scalars(
        select(RecordVersion).where(RecordVersion.record_id == record_id, RecordVersion.version == version)
    ).first()


def versions(s: Session, record_id: str) -> Sequence[RecordVersion]:
    q = select(RecordVersion).where(RecordVersion.record_id == record_id).order_by(RecordVersion.version)
    return s.scalars(q).all()


def images(s: Session, record_id: str, state: str | None = None) -> Sequence[Image]:
    q = select(Image).where(Image.record_id == record_id)
    if state:
        q = q.where(Image.state == state)
    return s.scalars(q.order_by(Image.version, Image.image_id)).all()


def save_pending(
    s: Session,
    rec: dict[str, Any],
    version_fields: dict[str, Any],
    image_rows: Iterable[tuple[str, int, str, str]],
    now: str,
) -> RecordVersion:
    """Insert or replace the pending (not finalised) version ``version_fields['version']`` of a record and
    its pending image rows ``(image_id, version, sha256, row_json)``. ``rec`` holds record_id, patient_code,
    study_key and state."""
    record = s.get(Record, rec["record_id"])
    if record is None:
        record = Record(**rec, latest_version=version_fields["version"], created_at=now, updated_at=now)
        s.add(record)
    record.state, record.updated_at = rec["state"], now
    record.latest_version = max(record.latest_version, version_fields["version"])
    s.flush()
    ver = get_version(s, rec["record_id"], version_fields["version"])
    if ver is not None and ver.finalised_at is not None:
        raise ValueError("a finalised version cannot be replaced")
    if ver is None:
        ver = RecordVersion(record_id=rec["record_id"], created_at=now, **version_fields)
        s.add(ver)
    else:
        for k, v in version_fields.items():
            setattr(ver, k, v)
        ver.hold_reason = ver.finding_category = ver.reviewer_id = ver.decided_at = None
    s.execute(delete(Image).where(Image.record_id == rec["record_id"], Image.state == PENDING))
    for image_id, version, sha, row_json in image_rows:
        s.add(
            Image(
                image_id=image_id,
                record_id=rec["record_id"],
                version=version,
                state=PENDING,
                row_json=row_json,
                sha256=sha,
            )
        )
    s.flush()
    return ver


def finalised_records(s: Session) -> Sequence[Record]:
    return s.scalars(
        select(Record).where(Record.finalised_version.is_not(None)).order_by(Record.record_id)
    ).all()


def finalised_versions(s: Session, record_id: str | None = None) -> Sequence[RecordVersion]:
    q = select(RecordVersion).where(RecordVersion.finalised_at.is_not(None))
    if record_id:
        q = q.where(RecordVersion.record_id == record_id)
    return s.scalars(q.order_by(RecordVersion.finalised_at, RecordVersion.id)).all()


def finalised_images(s: Session, record_id: str | None = None) -> Sequence[Image]:
    q = select(Image).where(Image.state == FINALISED)
    if record_id:
        q = q.where(Image.record_id == record_id)
    return s.scalars(q.order_by(Image.record_id, Image.version, Image.image_id)).all()


def c7_index(s: Session) -> dict[str, str]:
    """Finalised record_id and image_id → study_key of the study they belong to (C7)."""
    keys = dict(s.execute(select(Record.record_id, Record.study_key)).all())
    out = {
        rid: keys[rid]
        for rid in s.scalars(select(Record.record_id).where(Record.finalised_version.is_not(None)))
    }
    for image_id, rid in s.execute(select(Image.image_id, Image.record_id).where(Image.state == FINALISED)):
        out[image_id] = keys[rid]
    return out


def usage(s: Session) -> tuple[int, int]:
    """(finalised studies, finalised images) for the cohort cap (TR-COH-04)."""
    studies = s.scalar(select(func.count()).select_from(Record).where(Record.finalised_version.is_not(None)))
    imgs = s.scalar(select(func.count()).select_from(Image).where(Image.state == FINALISED))
    return int(studies or 0), int(imgs or 0)
