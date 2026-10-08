"""Request dependencies: the service context, the session, CSRF and ``require(permission)`` (SPEC §2, §7).
TR-ROLE-01..04, TR-SEC-01.

Every route that uses a session goes through ``session_dep``, which enforces the CSRF header on every
state-changing method. ``require`` admits only an ``active`` session and asks the services to check (and,
on refusal, audit) the permission.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from app.auth import sessions as sess
from app.auth.errors import Actor, AppError, unauthenticated
from app.services.auth import access, login
from app.services.context import ServiceContext

SESSION_COOKIE = "deid_session"
SETUP_COOKIE = "deid_setup"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_ctx(request: Request) -> ServiceContext:
    ctx: ServiceContext | None = getattr(request.app.state, "services", None)
    if ctx is None:
        raise AppError(503, "not_ready", "The app is starting.")
    return ctx


Ctx = Annotated[ServiceContext, Depends(get_ctx)]


def route_name(request: Request) -> str:
    route = request.scope.get("route")
    return f"{request.method} {getattr(route, 'path', '?')}"


def _session(request: Request, ctx: ServiceContext) -> sess.SessionInfo:
    info = login.current(ctx, request.cookies.get(SESSION_COOKIE))
    if info is None:
        raise unauthenticated()
    if request.method in UNSAFE:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not secrets.compare_digest(sent, info.csrf_token):
            raise AppError(403, "csrf", "Missing or invalid CSRF token.")
    return info


def session_dep(request: Request, ctx: Ctx) -> sess.SessionInfo:
    return _session(request, ctx)


def require(permission: str) -> Callable[..., Actor]:
    def dep(
        request: Request,
        ctx: Ctx,
        info: Annotated[sess.SessionInfo, Depends(session_dep)],
    ) -> Actor:
        if info.stage != sess.ACTIVE:
            raise AppError(403, "stage", f"Finish signing in first (current step: {info.stage}).")
        actor = Actor(info.user_id, info.roles, info.id)
        access.check(ctx, actor, permission, route_name(request))
        return actor

    dep.permission = permission  # type: ignore[attr-defined]  # read by the route-coverage test
    return dep
