"""Cookie helpers shared by the routers (HttpOnly, SameSite=Strict; no Secure flag: plain HTTP on
127.0.0.1 only, see DECISIONS)."""

from __future__ import annotations

from typing import Any

from fastapi import Response

from app.api.deps import SESSION_COOKIE
from app.auth.sessions import ABSOLUTE_SECONDS, Issued


def set_cookie(response: Response, name: str, value: str, path: str = "/") -> None:
    response.set_cookie(
        name, value, max_age=ABSOLUTE_SECONDS, path=path, httponly=True, samesite="strict", secure=False
    )


def session_body(response: Response, issued: Issued, **extra: Any) -> dict[str, Any]:
    """Put the new session token in the cookie; the body carries only the CSRF token and the next stage."""
    set_cookie(response, SESSION_COOKIE, issued.token)
    return {"stage": issued.stage, "csrf_token": issued.csrf_token, **extra}
