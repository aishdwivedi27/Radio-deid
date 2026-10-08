"""Status and first-run setup routes (SPEC §4.1, §7, §15.5). TR-ROLE-02, TR-COH-05.

Public by design: ``/api/status`` (setup and pre-approval flags for the banner) and ``/api/setup/*``,
which answer 410 for good once setup is complete.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.api.deps import SETUP_COOKIE, Ctx
from app.api.routes.common import set_cookie
from app.services.auth import setup
from app.services.governance import ethics

router = APIRouter(prefix="/api")
Role = Literal["admin", "custodian"]


class Account(BaseModel):
    username: str
    password: str
    attest_not_consultant: bool = False


@router.get("/status")
def status(ctx: Ctx) -> dict[str, Any]:
    st = setup.state(ctx)
    pre = ethics.is_preapproval(ctx)
    return {
        "setup_required": not st.complete,
        "setup_step": st.next_step,
        "preapproval": pre,
        "banner": ethics.PREAPPROVAL_BANNER if pre else None,
    }


@router.get("/setup")
def setup_state(ctx: Ctx) -> dict[str, Any]:
    st = setup.state(ctx)
    return {"complete": st.complete, "next_step": st.next_step}


@router.post("/setup/key")
def setup_key(request: Request, ctx: Ctx) -> dict[str, Any]:
    fingerprint = setup.create_key(ctx, request.cookies.get(SETUP_COOKIE))
    return {"key_fingerprint": fingerprint, "complete": True, "preapproval": ethics.is_preapproval(ctx)}


@router.post("/setup/{role}")
def setup_account(
    role: Role,
    body: Account,
    request: Request,
    response: Response,
    ctx: Ctx,
) -> dict[str, Any]:
    token, user_id = setup.create_first(
        ctx, role, body.username, body.password, request.cookies.get(SETUP_COOKIE), body.attest_not_consultant
    )
    set_cookie(response, SETUP_COOKIE, token, path="/api/setup")
    return {"user_id": user_id, "next_step": setup.state(ctx).next_step}
