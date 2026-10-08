"""App settings held in the DB (who changed what, when), and the minimal jobs table. TR-ROLE-04."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.store.models.auth import Job, Setting


def get(s: Session, key: str) -> str | None:
    row = s.get(Setting, key)
    return row.value if row else None


def put(s: Session, key: str, value: str, changed_by: str, now: str) -> str | None:
    """Set ``key``; returns the previous value."""
    row = s.get(Setting, key)
    old = row.value if row else None
    if row is None:
        s.add(Setting(key=key, value=value, changed_by=changed_by, changed_at=now))
    else:
        row.value, row.changed_by, row.changed_at = value, changed_by, now
    s.flush()
    return old


def add_job(s: Session, job_id: str, started_by: str, status: str, now: str) -> None:
    s.add(Job(job_id=job_id, started_by=started_by, status=status, created_at=now))
    s.flush()


def job_starter(s: Session, job_id: str) -> str | None:
    job = s.get(Job, job_id)
    return job.started_by if job else None
