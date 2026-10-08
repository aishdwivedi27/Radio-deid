"""What has been appended to each output file (SPEC §5.3; unique on (file, row_key)). TR-REL-NF-01."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.store.models.records import AppendLog


def logged_keys(s: Session, file: str) -> set[str]:
    return set(s.scalars(select(AppendLog.row_key).where(AppendLog.file == file)))


def max_offset(s: Session, file: str) -> int:
    return int(s.scalar(select(func.max(AppendLog.end_offset)).where(AppendLog.file == file)) or 0)


def count(s: Session, file: str) -> int:
    return int(s.scalar(select(func.count()).select_from(AppendLog).where(AppendLog.file == file)) or 0)


def add(s: Session, file: str, row_key: str, line_sha256: str, end_offset: int, now: str) -> None:
    s.add(
        AppendLog(file=file, row_key=row_key, line_sha256=line_sha256, end_offset=end_offset, appended_at=now)
    )
