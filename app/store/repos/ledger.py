"""Source-file ledger (SPEC §5.1 ``source_files``; D-010): duplicate files are ignored. TR-REL-NF-01."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.store.models.records import SourceFile


def finalised_keys(s: Session, record_id: str) -> tuple[set[str], set[str]]:
    """(sha256 set, sop_key set) of the record's source files that are in a finalised version."""
    rows = s.execute(
        select(SourceFile.sha256, SourceFile.sop_key).where(
            SourceFile.record_id == record_id, SourceFile.state == "finalised"
        )
    ).all()
    return {r[0] for r in rows}, {r[1] for r in rows}


def pending_keys(s: Session, record_id: str) -> tuple[set[str], set[str]]:
    rows = s.execute(
        select(SourceFile.sha256, SourceFile.sop_key).where(
            SourceFile.record_id == record_id, SourceFile.state == "pending"
        )
    ).all()
    return {r[0] for r in rows}, {r[1] for r in rows}


def drop_pending(s: Session, record_id: str) -> None:
    s.execute(delete(SourceFile).where(SourceFile.record_id == record_id, SourceFile.state == "pending"))


def add_pending(
    s: Session, record_id: str, version: int, files: Iterable[tuple[str, str]], job_id: str, now: str
) -> None:
    for sha, sop_key in files:
        if s.get(SourceFile, sha) is None:
            s.add(
                SourceFile(
                    sha256=sha,
                    record_id=record_id,
                    version=version,
                    sop_key=sop_key,
                    job_id=job_id,
                    state="pending",
                    seen_at=now,
                )
            )
    s.flush()


def finalise(s: Session, record_id: str, version: int) -> None:
    s.execute(
        update(SourceFile)
        .where(SourceFile.record_id == record_id, SourceFile.state == "pending")
        .values(state="finalised", version=version)
    )
