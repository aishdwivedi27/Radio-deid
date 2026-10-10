"""Start, cancel, claim and recover jobs (SPEC §2, §4, §6.5, §7). TR-ING-01, TR-ING-05, TR-SEC-03.

``jobs.run`` (Admin, Operator) starts a job; operators cancel their own jobs, an Admin any job. A folder job
stores where it reads from in ``secure/`` (folder names can be names) and only a hash of the root in the DB.
At startup, interrupted uploads are failed (staging deleted) and interrupted jobs are queued to resume.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from filelock import FileLock

from app.auth.errors import Actor, AppError, not_found
from app.auth.permissions import ADMIN
from app.services.auth import access
from app.services.context import ServiceContext, now_iso
from app.services.jobs import finish, inputs, secure_files
from app.store import audit
from app.store.models.jobs import Job
from app.store.output import lock as output_lock
from app.store.repos import jobs as jobs_repo


def new_job_id() -> str:
    return f"J{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def root_hash(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def start_folder(ctx: ServiceContext, actor: Actor, raw_path: str) -> dict[str, str]:
    access.check(ctx, actor, "jobs.run", "jobs.folder")
    fi = inputs.validate_folder(ctx, raw_path)
    approved = next(r.approved for r in inputs.resolve_roots(ctx) if r.current == fi.root)
    rel = fi.folder.relative_to(fi.root).as_posix()
    job_id = new_job_id()
    secure_files.write_source(ctx, job_id, str(approved), "" if rel == "." else rel)
    with ctx.db.transaction() as s:
        job = jobs_repo.add_job(s, job_id, actor.user_id, "queued", "folder", now_iso())
        job.root_hash = root_hash(str(approved))
        details = {"source_kind": "folder", "root_hash": job.root_hash[:16]}
        audit.append_event(s, "job.started", "job", job_id, details, actor.user_id)
    return {"job_id": job_id, "status": "queued", "source_kind": "folder"}


def begin_upload(ctx: ServiceContext, actor: Actor) -> str:
    access.check(ctx, actor, "jobs.run", "jobs.upload")
    job_id = new_job_id()
    with ctx.db.transaction() as s:
        jobs_repo.add_job(s, job_id, actor.user_id, "uploading", "upload", now_iso())
    return job_id


def upload_done(ctx: ServiceContext, actor: Actor, job_id: str, files: int, size: int) -> dict[str, object]:
    with ctx.db.transaction() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        job.status, job.upload_files, job.upload_bytes = "queued", files, size
        details = {"source_kind": "upload", "files": files, "bytes": size}
        audit.append_event(s, "job.started", "job", job_id, details, actor.user_id)
    return {"job_id": job_id, "status": "queued", "source_kind": "upload", "files": files, "bytes": size}


def upload_failed(ctx: ServiceContext, job_id: str, code: str) -> None:
    secure_files.delete_staging(ctx, job_id)
    with ctx.db.transaction() as s:
        job = jobs_repo.get_job(s, job_id)
        if job is not None:
            job.status, job.finished_at, job.error_code = "failed", now_iso(), code
            audit.append_event(s, "job.finished", "job", job_id, {"status": "failed", "error": code})


def visible_job(ctx: ServiceContext, actor: Actor, job_id: str) -> Job:
    """The job if the actor started it or is an Admin; otherwise 404 (no existence leak)."""
    with ctx.db.session() as s:
        job = jobs_repo.get_job(s, job_id)
    if job is None or not (job.started_by == actor.user_id or ADMIN in actor.roles):
        raise not_found("No such job.")
    return job


def cancel(ctx: ServiceContext, actor: Actor, job_id: str) -> dict[str, str]:
    access.check(ctx, actor, "jobs.run", "jobs.cancel")
    visible_job(ctx, actor, job_id)
    with ctx.db.transaction() as s:
        job = jobs_repo.get_job(s, job_id)
        assert job is not None
        if job.status in jobs_repo.TERMINAL:
            raise AppError(409, "job_ended", "This job has already ended.")
        job.cancel_requested = True
        if job.status == "uploading":
            raise AppError(409, "uploading", "The upload is still in progress.")
        queued = job.status == "queued"
    if queued:  # nothing has run yet: end it now
        return {"status": finish.cancel(ctx, job_id)}
    return {"status": "cancelling"}


def claim_next(ctx: ServiceContext) -> str | None:
    with ctx.db.transaction() as s:
        job = jobs_repo.next_queued(s)
        if job is None:
            return None
        job.status = "indexing"
        return job.job_id


def recover(ctx: ServiceContext) -> None:
    """Called once by the worker before it starts (SPEC §8: resumes after a crash or restart)."""
    with ctx.db.session() as s:
        uploading = [j.job_id for j in jobs_repo.jobs_with_status(s, ["uploading"])]
        active = [j.job_id for j in jobs_repo.jobs_with_status(s, jobs_repo.ACTIVE)]
    for job_id in uploading:
        upload_failed(ctx, job_id, "upload_interrupted")
    with ctx.db.transaction() as s:
        for job_id in active:
            job = jobs_repo.get_job(s, job_id)
            assert job is not None
            job.status = "queued"  # resumes first (oldest); cancel_requested is kept
    staging = ctx.paths.staging_dir
    if staging.is_dir():
        with ctx.db.session() as s:
            live = {
                j.job_id for j in jobs_repo.jobs_with_status(s, ["queued", "uploading", *jobs_repo.ACTIVE])
            }
        for folder in staging.iterdir():
            if folder.name not in live:
                secure_files.delete_staging(ctx, folder.name)


def worker_lock(ctx: ServiceContext) -> FileLock:
    return output_lock.worker_lock(ctx.paths.app_data_dir)
