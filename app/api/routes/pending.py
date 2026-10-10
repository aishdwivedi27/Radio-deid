"""Pending records (review queue), read-only (SPEC §2, §4, §6.3). TR-REV-01, TR-REV-06.

De-identified content only: rows, redacted report, findings, and PNGs rendered from the pending DICOM files.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response

from app.api.deps import Ctx, require
from app.auth.errors import Actor
from app.services.records import pending

router = APIRouter(prefix="/api/pending")
RecordId = Annotated[str, Path(pattern=r"^S[0-9A-F]{12}$")]
ImageId = Annotated[str, Path(pattern=r"^S[0-9A-F]{12}-\d{4}-\d{6}(-[a-z])?$")]


@router.get("")
def list_pending(
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("pending.view"))],
    state: str | None = None,
    job_id: str | None = None,
) -> list[dict[str, Any]]:
    return pending.list_pending(ctx, actor, state, job_id)


@router.get("/{record_id}")
def get_pending(
    record_id: RecordId, ctx: Ctx, actor: Annotated[Actor, Depends(require("pending.view"))]
) -> dict[str, Any]:
    return pending.get_pending(ctx, actor, record_id)


@router.get("/{record_id}/preview.png")
def preview(
    record_id: RecordId, ctx: Ctx, actor: Annotated[Actor, Depends(require("pending.view"))]
) -> Response:
    return Response(pending.preview_png(ctx, actor, record_id), media_type="image/png")


@router.get("/{record_id}/image/{image_id}.png")
def image(
    record_id: RecordId,
    image_id: ImageId,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("pending.view"))],
) -> Response:
    return Response(pending.image_png(ctx, actor, record_id, image_id), media_type="image/png")
