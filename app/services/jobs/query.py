"""Read side of jobs: list, summary, events, folder names, reconciliation (SPEC §2, §6.4). TR-ELIG-14,
TR-ELIG-15.

Operators see their own jobs, Admins all jobs. Folder names (which can be patient names) come only from
``secure/``: through ``folders`` for the job's owner and Admins, and with the skip list for the Custodian
and Reviewer (``spotcheck.py``). The reconciliation CSV is for the Custodian and Reviewer.
"""

from __future__ import annotations

from typing import Any

from app.auth.errors import Actor, not_found
from app.auth.permissions import ADMIN
from app.services.auth import access
from app.services.context import ServiceContext
from app.services.jobs import events, finish, secure_files
from app.services.jobs.create import visible_job
from app.store.repos import jobs as jobs_repo

EXCLUSIONS_VIEW = "exclusions.view"


def list_jobs(ctx: ServiceContext, actor: Actor) -> list[dict[str, Any]]:
    access.check(ctx, actor, "jobs.run", "jobs.list")
    with ctx.db.session() as s:
        rows = jobs_repo.list_jobs(s, None if ADMIN in actor.roles else actor.user_id)
        ids = [(j.job_id, j.source_kind, j.status, j.started_by, j.created_at, j.started_at, j.finished_at)
               for j in rows]  # fmt: skip
    out = []
    for job_id, kind, status, by, created, started, finished in ids:
        sm = finish.summary_of(ctx, job_id)
        out.append(
            {
                "job_id": job_id,
                "source_kind": kind,
                "status": status,
                "started_by": by,
                "created_at": created,
                "started_at": started,
                "finished_at": finished,
                "done": sm["done"],
                "total": sm["total"],
                "counts": sm["counts"],
            }  # fmt: skip
        )
    return out


def get_job(ctx: ServiceContext, actor: Actor, job_id: str) -> dict[str, Any]:
    access.check(ctx, actor, "jobs.run", "jobs.get")
    job = visible_job(ctx, actor, job_id)
    return {
        **finish.summary_of(ctx, job_id),
        "started_by": job.started_by,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error_code,
        "cancel_requested": job.cancel_requested,
    }


def open_events(ctx: ServiceContext, actor: Actor, job_id: str) -> None:
    access.check(ctx, actor, "jobs.run", "jobs.events")
    visible_job(ctx, actor, job_id)


def events_since(ctx: ServiceContext, job_id: str, seq: int) -> list[events.Event]:
    return events.since(ctx, job_id, seq)


def progress(ctx: ServiceContext, job_id: str) -> dict[str, Any]:
    return finish.summary_of(ctx, job_id)


def folders(ctx: ServiceContext, actor: Actor, job_id: str) -> dict[str, str]:
    """Folder key → name (SPEC §6.4: shown in the app only, never in logs, audit events or exports)."""
    access.check(ctx, actor, "jobs.run", "jobs.folders")
    visible_job(ctx, actor, job_id)
    return secure_files.read_folders(ctx, job_id)


def reconciliation_csv(ctx: ServiceContext, actor: Actor, job_id: str) -> bytes:
    access.check(ctx, actor, EXCLUSIONS_VIEW, "jobs.reconciliation")
    _exists(ctx, job_id)
    path = secure_files.reconciliation_path(ctx, job_id)
    if not path.is_file():
        raise not_found("The reconciliation is written when the job ends.")
    return path.read_bytes()


def _exists(ctx: ServiceContext, job_id: str) -> None:
    with ctx.db.session() as s:
        if jobs_repo.get_job(s, job_id) is None:
            raise not_found("No such job.")
