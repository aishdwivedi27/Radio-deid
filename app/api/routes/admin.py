"""User administration, technical settings and key routes (SPEC §2, §7). TR-ROLE-01..04. T17."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.deps import Ctx, require
from app.auth.errors import Actor
from app.services.auth import keys, users
from app.services.auth import settings as tech

router = APIRouter(prefix="/api")


class NewUser(BaseModel):
    username: str
    roles: list[str]
    is_consultant: bool = False


class Roles(BaseModel):
    roles: list[str]


class CustodianGrant(BaseModel):
    password: str  # the signed-in Custodian's own password, typed again (CR-01)
    attest_centre_staff: bool = False


class InputRoots(BaseModel):
    input_roots: list[str]


class Toggle(BaseModel):
    enabled: bool


class StepUp(BaseModel):
    password: str  # the signed-in Custodian's own password, typed again (CR-01)
    confirm: str | None = None


@router.get("/users")
def list_users(
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> list[dict[str, Any]]:
    return [asdict(u) for u in users.list_users(ctx, actor)]


@router.post("/users")
def create_user(
    body: NewUser,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> dict[str, Any]:
    view, temporary = users.create_user(ctx, actor, body.username, body.roles, body.is_consultant)
    return {"user": asdict(view), "temporary_password": temporary}


@router.put("/users/{user_id}/roles")
def set_roles(
    user_id: str,
    body: Roles,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> dict[str, Any]:
    return asdict(users.set_roles(ctx, actor, user_id, body.roles))


@router.post("/users/{user_id}/disable")
def disable(
    user_id: str,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> dict[str, Any]:
    return asdict(users.set_disabled(ctx, actor, user_id, True))


@router.post("/users/{user_id}/enable")
def enable(
    user_id: str,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> dict[str, Any]:
    return asdict(users.set_disabled(ctx, actor, user_id, False))


@router.post("/users/{user_id}/unlock")
def unlock(
    user_id: str,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> dict[str, Any]:
    return asdict(users.unlock(ctx, actor, user_id))


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("users.manage"))],
) -> dict[str, Any]:
    return {"temporary_password": users.reset_password(ctx, actor, user_id)}


@router.post("/users/{user_id}/custodian")
def grant_custodian(
    user_id: str,
    body: CustodianGrant,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("custodian.grant"))],
) -> dict[str, Any]:
    return asdict(users.grant_custodian(ctx, actor, user_id, body.password, body.attest_centre_staff))


@router.get("/settings")
def get_settings(
    request: Request,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("settings.technical"))],
) -> dict[str, Any]:
    return tech.view(ctx, actor, request.app.state.settings.input_roots)


@router.put("/settings/input-roots")
def put_input_roots(
    body: InputRoots,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("settings.technical"))],
) -> dict[str, Any]:
    return {"input_roots": tech.set_input_roots(ctx, actor, body.input_roots)}


@router.put("/settings/separation-of-duties")
def put_sod(
    body: Toggle,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("settings.technical"))],
) -> dict[str, Any]:
    return {"sod_enabled": tech.set_sod(ctx, actor, body.enabled)}


@router.get("/key/fingerprint")
def key_fingerprint(
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("key.manage"))],
) -> dict[str, Any]:
    return {"key_fingerprint": keys.fingerprint(ctx, actor)}


@router.post("/key/backup")
def key_backup(
    body: StepUp,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("key.manage"))],
) -> dict[str, Any]:
    keys.request_backup(ctx, actor, body.password)
    return {}


@router.post("/key/rotate")
def key_rotate(
    body: StepUp,
    ctx: Ctx,
    actor: Annotated[Actor, Depends(require("key.manage"))],
) -> dict[str, Any]:
    keys.request_rotation(ctx, actor, body.password, body.confirm)
    return {}
