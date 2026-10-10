"""How a job ends: finished / reconcile_failed, cancelled (with rollback) or failed (SPEC §4, §6.4, §7).
TR-ELIG-11, TR-ELIG-12, TR-ELIG-14, TR-ELIG-15, TR-SEC-03.

Every ending writes ``reconciliation.csv``, deletes ``staging/<job_id>/`` and is audited. A finished job also
writes ``exclusions.csv``. A totals mismatch marks the job ``reconcile_failed`` (job summary and audit).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.deid.record.canonical import sha256_file
from app.services.context import ServiceContext, now_iso
from app.services.jobs import events, rollback, secure_files, summary
from app.store import audit
from app.store.repos import jobs as jobs_repo

if TYPE_CHECKING:
    from app.services.jobs.run import Indexed

log = logging.getLogger(__name__)


def _summary(ctx: ServiceContext, job_id: str) -> tuple[summary.Summary, dict[str, Any]]:
    with ctx.db.session() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        studies = jobs_repo.studies(s, job_id)
        return summary.summarise(job, studies), summary.job_summary(job, studies)


def summary_of(ctx: ServiceContext, job_id: str) -> dict[str, Any]:
    return _summary(ctx, job_id)[1]


def indexed_payload(ctx: ServiceContext, job_id: str) -> dict[str, Any]:
    sm, js = _summary(ctx, job_id)
    return {
        "studies": js["total"],
        "files": int(sm.index.get("files", 0)),
        "reports_found": js["reports_found"],
        "reports_unmatched": js["reports_unmatched"],
        "folders": [{"key": f.key, "studies": f.found, "files": f.files} for f in sm.folders],
    }


def _write_reconciliation(ctx: ServiceContext, job_id: str) -> tuple[bool, dict[str, Any]]:
    sm, js = _summary(ctx, job_id)
    names = secure_files.read_folders(ctx, job_id)
    rows = summary.reconciliation_rows(sm, names)
    secure_files.write_reconciliation(ctx, job_id, summary.reconciliation_columns(), rows)
    return sm.balanced, js


def _exclusion_rows(ctx: ServiceContext, job_id: str, ix: Indexed) -> list[dict[str, Any]]:
    out = []
    with ctx.db.session() as s:
        studies = [st for st in jobs_repo.studies(s, job_id) if st.status == "excluded" and st.rule_id]
    for st in studies:
        _, group = ix.groups[st.study_key]
        files = sorted(f.path for f in group.files)
        detail = json.loads(st.detail_json or "{}")
        folders = sorted({p.parent.relative_to(ix.base).as_posix() for p in files})
        out.append(
            {
                "job_id": job_id,
                "study_key": st.study_key,
                "source_path": ";".join(folders),
                "file_names": ";".join(p.name for p in files),
                "file_count": len(files),
                "file_sha256_list": ";".join(sha256_file(p) for p in files),
                "modality": detail.get("modality", ""),
                "reason": st.reason,
                "rule_id": st.rule_id,
                "rules_version": detail.get("rules_version", ""),
                "evidence": detail.get("evidence", ""),
                "other_matches": ";".join(detail.get("other_matches") or []),
                "screened_at": detail.get("screened_at", ""),
            }
        )
    return out


def _end(ctx: ServiceContext, job_id: str, status: str, error_code: str | None = None) -> None:
    with ctx.db.transaction() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        job.status, job.finished_at, job.error_code = status, now_iso(), error_code
    secure_files.delete_staging(ctx, job_id)


def _audit(ctx: ServiceContext, action: str, job_id: str, details: dict[str, Any]) -> None:
    with ctx.db.transaction() as s:
        audit.append_event(s, action, "job", job_id, details)


def complete(ctx: ServiceContext, job_id: str, ix: Indexed) -> str:
    secure_files.write_exclusions(ctx, job_id, _exclusion_rows(ctx, job_id, ix))
    balanced, js = _write_reconciliation(ctx, job_id)
    status = "finished" if balanced else "reconcile_failed"
    _end(ctx, job_id, status)
    details = {"status": status, "counts": js["counts"], "by_reason": js["by_reason"], "balanced": balanced}
    _audit(ctx, "job.finished", job_id, details)
    events.emit(
        ctx, job_id, "finished", {"status": status, "balanced": balanced, "summary": summary_of(ctx, job_id)}
    )
    log.info("job %s %s", job_id, status)
    return status


def cancel(ctx: ServiceContext, job_id: str) -> str:
    with ctx.db.transaction() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        job.status = "cancelling"
    done = rollback.rollback_job(ctx, job_id)
    secure_files.remove_exclusions(ctx, job_id)
    _write_reconciliation(ctx, job_id)
    _end(ctx, job_id, "cancelled")
    details = {"rolled_back_records": done.records, "rolled_back_exclusions": done.exclusions}
    _audit(ctx, "job.cancelled", job_id, details)
    events.emit(
        ctx, job_id, "cancelled", {"rolled_back_records": done.records, "summary": summary_of(ctx, job_id)}
    )
    return "cancelled"


def fail(ctx: ServiceContext, job_id: str, code: str, message: str) -> str:
    _write_reconciliation(ctx, job_id)
    _end(ctx, job_id, "failed", code)
    _audit(ctx, "job.finished", job_id, {"status": "failed", "error": code})
    events.emit(ctx, job_id, "error", {"code": code, "message": message})
    return "failed"
