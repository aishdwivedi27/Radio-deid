"""Ethics approval, patient lists and the audit log (SPEC §2, §7, §12). TR-COH-05, TR-LIST-01..05,
TR-SEC-03."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import Ctx, require
from app.auth.errors import Actor
from app.services.auth import audit_view
from app.services.auth.audit_view import AuditFilter
from app.services.governance import ethics, lists

router = APIRouter(prefix="/api")


class Approval(BaseModel):
    password: str  # the Custodian's own password, typed again (CR-01)
    committee_name: str
    approval_ref: str
    protocol_version: str
    approval_date: dt.date
    expiry_date: dt.date
    archive_start: dt.date
    waiver_cutoff: dt.date
    cohort_cap: int = Field(ge=0)
    cap_unit: str
    notice_start: dt.date
    optout_window_days: int
    legal_opinion: bool = False
    legal_opinion_date: dt.date | None = None
    ec_amendment_refs: list[str] = []


@router.post("/ethics/approval")
def record_approval(
    body: Approval,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("ethics.configure"))],
) -> dict[str, Any]:
    fields = body.model_dump(exclude={"password", "ec_amendment_refs"})
    data = ethics.ApprovalInput(**fields, ec_amendment_refs=tuple(body.ec_amendment_refs))
    approval_id = ethics.configure_approval(ctx, actor, data, body.password)
    return {"approval_id": approval_id, "preapproval": ethics.is_preapproval(ctx)}


@router.post("/lists/{list_type}")
async def import_list(
    list_type: str,
    request: Request,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("lists.import"))],
) -> dict[str, Any]:
    body = await request.body()
    result = lists.import_list(ctx, actor, list_type.upper(), body)
    return {"list_version": result.list_version, "withdrawn": len(result.withdrawn)}


def _filter(
    ts_from: str | None = Query(None, alias="from"),
    ts_to: str | None = Query(None, alias="to"),
    user_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> AuditFilter:
    return AuditFilter(ts_from, ts_to, user_id, action, target_type, target_id)


@router.get("/audit")
def audit_list(
    f: Annotated[AuditFilter, Depends(_filter)],
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("audit.view"))],
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return audit_view.list_events(ctx, actor, f, limit, offset)


@router.get("/audit/export.csv")
def audit_export(
    f: Annotated[AuditFilter, Depends(_filter)],
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("audit.view"))],
) -> StreamingResponse:
    rows = audit_view.export_csv(ctx, actor, f)
    headers = {"Content-Disposition": 'attachment; filename="audit_log.csv"'}
    return StreamingResponse(rows, media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/audit/verify")
def audit_verify(
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("audit.view"))],
) -> dict[str, Any]:
    return audit_view.verify(ctx, actor).as_dict()
