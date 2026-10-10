"""Jobs, job studies and job events (SPEC §4, §6.4). TR-ING-01..05, TR-ELIG-12..15."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.store.models.jobs import Job, JobEvent, JobStudy

TERMINAL = frozenset({"finished", "reconcile_failed", "cancelled", "failed"})
ACTIVE = frozenset({"indexing", "processing", "cancelling"})


def add_job(s: Session, job_id: str, started_by: str, status: str, source_kind: str, now: str) -> Job:
    job = Job(
        job_id=job_id,
        started_by=started_by,
        status=status,
        created_at=now,
        source_kind=source_kind,
        cancel_requested=False,
        upload_files=0,
        upload_bytes=0,
        index_json="{}",
    )
    s.add(job)
    s.flush()
    return job


def get_job(s: Session, job_id: str) -> Job | None:
    return s.get(Job, job_id)


def list_jobs(s: Session, started_by: str | None = None) -> Sequence[Job]:
    q = select(Job)
    if started_by is not None:
        q = q.where(Job.started_by == started_by)
    return s.scalars(q.order_by(Job.created_at.desc(), Job.job_id.desc())).all()


def jobs_with_status(s: Session, statuses: Iterable[str]) -> Sequence[Job]:
    q = select(Job).where(Job.status.in_(tuple(statuses))).order_by(Job.created_at, Job.job_id)
    return s.scalars(q).all()


def next_queued(s: Session) -> Job | None:
    return s.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.job_id)).first()


# --- studies ---------------------------------------------------------------------------------------------


def studies(s: Session, job_id: str) -> Sequence[JobStudy]:
    return s.scalars(select(JobStudy).where(JobStudy.job_id == job_id).order_by(JobStudy.ordinal)).all()


def get_study(s: Session, job_id: str, study_key: str) -> JobStudy | None:
    q = select(JobStudy).where(JobStudy.job_id == job_id, JobStudy.study_key == study_key)
    return s.scalars(q).first()


def add_study(s: Session, job_id: str, fields: dict[str, Any], now: str) -> JobStudy:
    """Insert the study unless the job already has it (resume keeps the earlier outcome)."""
    found = get_study(s, job_id, fields["study_key"])
    if found is not None:
        found.ordinal, found.folder_key = fields["ordinal"], fields["folder_key"]
        found.spans_folders, found.n_files = fields["spans_folders"], fields["n_files"]
        return found
    row = JobStudy(job_id=job_id, status="not_done", updated_at=now, **fields)
    s.add(row)
    s.flush()
    return row


# --- events ----------------------------------------------------------------------------------------------


def add_event(s: Session, job_id: str, type_: str, payload: dict[str, Any], now: str) -> int:
    last = s.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    seq = int(last or 0) + 1
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    s.add(JobEvent(job_id=job_id, seq=seq, type=type_, payload_json=body, ts=now))
    s.flush()
    return seq


def events_after(s: Session, job_id: str, seq: int, limit: int = 500) -> Sequence[JobEvent]:
    q = select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.seq > seq)
    return s.scalars(q.order_by(JobEvent.seq).limit(limit)).all()
