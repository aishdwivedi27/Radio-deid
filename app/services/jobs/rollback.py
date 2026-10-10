"""Cancel rolls back what a job produced (owner decision, 10 Oct 2026). TR-ING-04.

Only pending versions this job last wrote and that nobody has decided on (``awaiting_review`` or
``auto_qa_failed``) are discarded: the version row, its pending image rows, its pending ledger rows and its
``work/pending/<record_id>/`` folder. A record left with no version is deleted; a record with a finalised
version goes back to it. The job's exclusion rows go too. Audit events are never removed, and finalised or
decided versions are never touched. Afterwards every study of the job is ``not_done``, so running the input
again starts from the first study.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.services.context import ServiceContext, now_iso
from app.store.models.records import Exclusion, Image, Record, RecordVersion
from app.store.repos import jobs as jobs_repo
from app.store.repos import ledger
from app.store.repos import records as recs

UNDECIDED = ("awaiting_review", "auto_qa_failed")


@dataclass(frozen=True)
class Rollback:
    records: int
    exclusions: int


def _discard_version(s: Session, job_id: str, record_id: str, version: int) -> bool:
    ver = recs.get_version(s, record_id, version)
    if ver is None or ver.finalised_at is not None or ver.state not in UNDECIDED or ver.job_id != job_id:
        return False
    s.execute(delete(Image).where(Image.record_id == record_id, Image.state == recs.PENDING))
    ledger.drop_pending(s, record_id)
    s.delete(ver)
    s.flush()
    record = s.get(Record, record_id)
    remaining = recs.versions(s, record_id)
    if record is not None and not remaining:
        s.delete(record)
    elif record is not None:
        latest: RecordVersion = remaining[-1]
        record.latest_version = latest.version
        record.state = "finalised" if latest.finalised_at is not None else latest.state
        if record.state == "finalised" and record.finalised_version is None:
            record.finalised_version = latest.version
        record.updated_at = now_iso()
    return True


def rollback_job(ctx: ServiceContext, job_id: str) -> Rollback:
    with ctx.db.session() as s:
        touched = [
            (st.record_id, st.version)
            for st in jobs_repo.studies(s, job_id)
            if st.wrote_version and st.version is not None
        ]
    removed = 0
    for record_id, version in touched:
        assert version is not None
        with ctx.db.transaction() as s:
            gone = _discard_version(s, job_id, record_id, version)
        if gone:
            shutil.rmtree(ctx.paths.pending_root / record_id, ignore_errors=True)
            removed += 1
    with ctx.db.transaction() as s:
        result = s.execute(delete(Exclusion).where(Exclusion.job_id == job_id))
        n_excl = int(getattr(result, "rowcount", 0) or 0)
        for st in jobs_repo.studies(s, job_id):
            st.status, st.version, st.wrote_version = "not_done", None, False
            st.reason = st.rule_id = None
            st.with_report, st.detail_json, st.updated_at = False, "{}", now_iso()
    return Rollback(removed, n_excl)
