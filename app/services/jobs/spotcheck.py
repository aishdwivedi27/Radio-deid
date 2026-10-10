"""Skip spot-check: open a study the screen excluded and record whether the skip was right (SPEC §6.4).
TR-ELIG-11, TR-ELIG-13.

Custodian and Reviewer only. The skip list carries plain source paths (identifying data, kept in
``secure/``); ``open_path`` adds where the study is now (the input root found again) so staff can open it in
their DICOM viewer. Upload jobs have no ``open_path``: their staging copy is deleted when the job ends. The
``exclusion.verified`` audit event carries the study key, rule and result only, never the path or the note.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.auth.errors import Actor, AppError, bad_request, not_found
from app.services.auth import access
from app.services.context import ServiceContext, now_iso
from app.services.jobs import inputs, secure_files
from app.store import audit
from app.store.repos import jobs as jobs_repo

PERMISSION = "exclusions.view"
RESULTS = ("confirmed", "wrong_skip")
MAX_NOTE = 1000


def _source_kind(ctx: ServiceContext, job_id: str) -> str:
    with ctx.db.session() as s:
        job = jobs_repo.get_job(s, job_id)
    if job is None:
        raise not_found("No such job.")
    return job.source_kind


def _input_folder(ctx: ServiceContext, job_id: str) -> Path | None:
    source = secure_files.read_source(ctx, job_id) or {}
    for r in inputs.resolve_roots(ctx):
        if str(r.approved) == source.get("approved_root") and r.current is not None:
            rel = source.get("rel", "")
            return r.current / rel if rel else r.current
    return None


def list_exclusions(ctx: ServiceContext, actor: Actor, job_id: str) -> dict[str, Any]:
    access.check(ctx, actor, PERMISSION, "jobs.exclusions")
    kind = _source_kind(ctx, job_id)
    rows: list[dict[str, Any]] = [dict(r) for r in secure_files.read_exclusions(ctx, job_id)]
    base = _input_folder(ctx, job_id) if kind == "folder" else None
    for row in rows:
        paths = [p for p in str(row.get("source_path", "")).split(";") if p]
        row["open_path"] = [str((base / p).resolve()) for p in paths] if base is not None else []
    return {"job_id": job_id, "folders": secure_files.read_folders(ctx, job_id), "rows": rows}


def exclusions_csv(ctx: ServiceContext, actor: Actor, job_id: str) -> bytes:
    access.check(ctx, actor, PERMISSION, "jobs.exclusions_csv")
    _source_kind(ctx, job_id)
    path = secure_files.exclusions_path(ctx, job_id)
    if not path.is_file():
        raise not_found("The skip list is written when the job ends.")
    return path.read_bytes()


def verify(ctx: ServiceContext, actor: Actor, job_id: str, study_key: str, result: str, note: str) -> None:
    access.check(ctx, actor, PERMISSION, "jobs.exclusions.verify")
    _source_kind(ctx, job_id)
    if result not in RESULTS:
        raise bad_request("result must be confirmed or wrong_skip", "result")
    if len(note or "") > MAX_NOTE:
        raise bad_request("the note is too long", "note")
    row = next(
        (r for r in secure_files.read_exclusions(ctx, job_id) if r.get("study_key") == study_key), None
    )
    if row is None:
        raise AppError(404, "not_found", "That study is not on this job's skip list.")
    rule = row.get("rule_id", "")
    secure_files.append_spotcheck(
        ctx,
        job_id,
        {
            "study_key": study_key,
            "rule_id": rule,
            "result": result,
            "note": note or "",
            "checked_by": actor.user_id,
            "checked_at": now_iso(),
        },  # fmt: skip
    )
    with ctx.db.transaction() as s:
        details = {"study_key": study_key, "rule_id": rule, "result": result, "job_id": job_id}
        audit.append_event(s, "exclusion.verified", "exclusion", study_key[:16], details, actor.user_id)
