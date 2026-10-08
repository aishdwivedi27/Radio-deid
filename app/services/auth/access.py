"""Permission checks with an audited denial, and TOTP step-up (SPEC §2, §7). TR-ROLE-01..04, TR-SEC-01.

Every denial writes ``access.denied`` (user, permission, route; no values) in its own transaction, so the
event survives the error that follows (T17).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth import sessions as sess
from app.auth import totp
from app.auth.errors import Actor, AppError, forbidden, locked
from app.auth.permissions import has_permission
from app.services.context import ServiceContext
from app.store import audit
from app.store.models.auth import User
from app.store.repos import users as repo

TOTP_FAILED = "The authenticator code is not valid."


def deny(ctx: ServiceContext, actor: Actor | None, permission: str, where: str) -> AppError:
    """Audit a refused action and return the error to raise."""
    with ctx.db.transaction() as s:
        audit.append_event(
            s,
            "access.denied",
            "permission",
            permission,
            {"route": where[:80]},
            actor.user_id if actor else None,
        )
    return forbidden()


def check(ctx: ServiceContext, actor: Actor, permission: str, where: str) -> None:
    if not has_permission(actor.roles, permission):
        raise deny(ctx, actor, permission, where)


def _accept_code(s: Session, user: User, code: str) -> bool:
    step = totp.matching_step(user.totp_secret or "", code, user.totp_last_step) if user.totp_secret else None
    if step is None:
        sess.record_failure(user)
        return False
    user.totp_last_step = step
    return True


def verify_step_up(ctx: ServiceContext, actor: Actor, code: str | None, permission: str, where: str) -> None:
    """A fresh TOTP code for a sensitive Custodian action. A wrong code counts towards the lockout and is
    audited as a denial; it is committed before the error is raised."""
    with ctx.db.transaction() as s:
        user = repo.get_user(s, actor.user_id)
        if user is None:
            raise forbidden()
        if sess.is_locked(user):
            raise locked()
        ok = _accept_code(s, user, code or "")
        if not ok:
            audit.append_event(
                s, "access.denied", "permission", permission, {"route": where[:80], "totp": "invalid"},
                actor.user_id,
            )  # fmt: skip
    if not ok:
        raise AppError(403, "totp_invalid", TOTP_FAILED)
