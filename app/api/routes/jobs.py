"""Jobs: start by folder or upload, watch, cancel; skip list and reconciliation (SPEC §2, §6.4, §6.5, §7).
TR-ING-01..05, TR-ELIG-10..15.

Thin: each route checks the role and calls one service. Responses carry IDs and counts; folder names and
source paths appear only in ``/folders`` (job owner, Admin) and ``/exclusions*`` (Custodian, Reviewer).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.api import sse
from app.api.deps import Ctx, require
from app.auth.errors import Actor
from app.services.jobs import create, query, spotcheck, upload

router = APIRouter(prefix="/api/jobs")
JobId = Annotated[str, Path(pattern=r"^J\d{8}-\d{6}-[0-9a-f]{4}$")]
StudyKey = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
CSV = "text/csv; charset=utf-8"


class FolderJob(BaseModel):
    path: str


class Verify(BaseModel):
    result: str
    note: str = ""


@router.post("/folder", status_code=202)
def start_folder(
    body: FolderJob, ctx: Ctx, actor: Annotated[Actor, Depends(require("jobs.run"))]
) -> dict[str, str]:
    return create.start_folder(ctx, actor, body.path)


@router.post("/upload", status_code=202)
async def start_upload(
    request: Request, ctx: Ctx, actor: Annotated[Actor, Depends(require("jobs.run"))]
) -> dict[str, object]:
    length = request.headers.get("content-length")
    declared = int(length) if length and length.isdigit() else None
    ctype = request.headers.get("content-type", "")
    return await upload.receive(ctx, actor, ctype, declared, request.stream())


@router.get("")
def list_jobs(ctx: Ctx, actor: Annotated[Actor, Depends(require("jobs.run"))]) -> list[dict[str, Any]]:
    return query.list_jobs(ctx, actor)


@router.get("/{job_id}")
def get_job(job_id: JobId, ctx: Ctx, actor: Annotated[Actor, Depends(require("jobs.run"))]) -> dict[str, Any]:
    return query.get_job(ctx, actor, job_id)


@router.post("/{job_id}/cancel", status_code=202)
def cancel(job_id: JobId, ctx: Ctx, actor: Annotated[Actor, Depends(require("jobs.run"))]) -> dict[str, str]:
    return create.cancel(ctx, actor, job_id)


@router.get("/{job_id}/events")
def events(
    job_id: JobId,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("jobs.run"))],
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    query.open_events(ctx, actor, job_id)
    last = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    return StreamingResponse(sse.stream(ctx, job_id, last), media_type="text/event-stream", headers=headers)


@router.get("/{job_id}/folders")
def folders(job_id: JobId, ctx: Ctx, actor: Annotated[Actor, Depends(require("jobs.run"))]) -> dict[str, str]:
    return query.folders(ctx, actor, job_id)


@router.get("/{job_id}/reconciliation.csv")
def reconciliation(
    job_id: JobId, ctx: Ctx, actor: Annotated[Actor, Depends(require("exclusions.view"))]
) -> Response:
    return Response(query.reconciliation_csv(ctx, actor, job_id), media_type=CSV)


@router.get("/{job_id}/exclusions")
def exclusions(
    job_id: JobId, ctx: Ctx, actor: Annotated[Actor, Depends(require("exclusions.view"))]
) -> dict[str, Any]:
    return spotcheck.list_exclusions(ctx, actor, job_id)


@router.get("/{job_id}/exclusions.csv")
def exclusions_csv(
    job_id: JobId, ctx: Ctx, actor: Annotated[Actor, Depends(require("exclusions.view"))]
) -> Response:
    return Response(spotcheck.exclusions_csv(ctx, actor, job_id), media_type=CSV)


@router.post("/{job_id}/exclusions/{study_key}/verify")
def verify(
    job_id: JobId,
    study_key: StudyKey,
    body: Verify,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("exclusions.view"))],
) -> dict[str, str]:
    spotcheck.verify(ctx, actor, job_id, study_key, body.result, body.note)
    return {"status": "recorded"}
