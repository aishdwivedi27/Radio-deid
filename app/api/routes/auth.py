"""Sign-in routes (SPEC §7). TR-SEC-01.

Login and the second factor issue a new session token at every step (the cookie); the body carries only the
next stage and the CSRF token. ``/me`` returns the CSRF token again after a page reload.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.api.deps import SESSION_COOKIE, Ctx, session_dep, stage_dep
from app.api.routes.common import session_body
from app.auth import sessions as sess
from app.auth.permissions import PERMISSIONS, has_permission
from app.services.auth import login

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    username: str
    password: str


class Code(BaseModel):
    code: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
def do_login(
    body: Credentials,
    request: Request,
    response: Response,
    ctx: Ctx,
) -> dict[str, Any]:
    old = login.current(ctx, request.cookies.get(SESSION_COOKIE))
    step = login.login(ctx, body.username, body.password, old.id if old else None)
    return session_body(response, step.issued)


@router.post("/totp")
def do_second_factor(
    body: Code,
    response: Response,
    ctx: Ctx,
    info: Annotated[sess.SessionInfo, Depends(stage_dep(sess.MFA_PENDING))],
) -> dict[str, Any]:
    return session_body(response, login.second_factor(ctx, info, body.code).issued)


@router.post("/password")
def do_change_password(
    body: PasswordChange,
    response: Response,
    ctx: Ctx,
    info: Annotated[sess.SessionInfo, Depends(stage_dep(sess.PASSWORD_CHANGE, sess.ACTIVE))],
) -> dict[str, Any]:
    step = login.change_password(ctx, info, body.current_password, body.new_password)
    return session_body(response, step.issued)


@router.post("/totp/enrol")
def do_start_enrolment(
    ctx: Ctx,
    info: Annotated[sess.SessionInfo, Depends(stage_dep(sess.TOTP_ENROL, sess.ACTIVE))],
) -> dict[str, Any]:
    secret, uri = login.start_enrolment(ctx, info.user_id)
    return {"totp_secret": secret, "otpauth_uri": uri}


@router.post("/totp/confirm")
def do_confirm_enrolment(
    body: Code,
    response: Response,
    ctx: Ctx,
    info: Annotated[sess.SessionInfo, Depends(stage_dep(sess.TOTP_ENROL, sess.ACTIVE))],
) -> dict[str, Any]:
    step = login.enrol_in_session(ctx, info, body.code)
    return session_body(response, step.issued, backup_codes=list(step.backup_codes))


@router.post("/logout")
def do_logout(
    response: Response,
    ctx: Ctx,
    info: Annotated[sess.SessionInfo, Depends(session_dep)],
) -> dict[str, Any]:
    login.logout(ctx, info)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(info: Annotated[sess.SessionInfo, Depends(session_dep)]) -> dict[str, Any]:
    active = info.stage == sess.ACTIVE
    return {
        "user_id": info.user_id,
        "username": info.username,
        "roles": sorted(info.roles),
        "stage": info.stage,
        "csrf_token": info.csrf_token,
        "permissions": sorted(p for p in PERMISSIONS if active and has_permission(info.roles, p)),
    }
