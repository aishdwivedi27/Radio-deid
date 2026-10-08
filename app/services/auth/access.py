"""Permission checks with an audited denial, and password re-entry for sensitive actions (SPEC §2, §7;
CR-01). TR-ROLE-01..04, TR-SEC-01.

Every denial writes ``access.denied`` (user, permission, route; no values) in its own transaction, so the
event survives the error that follows (T17). Custodian actions (ethics approval, Custodian grant, key backup
and rotation) ask for the user's password again; a wrong one counts towards the lockout (CR-01).
"""

from __future__ import annotations

from app.auth import passwords as pw
from app.auth import sessions as sess
from app.auth.errors import Actor, AppError, forbidden, locked
from app.auth.permissions import has_permission
from app.services.context import ServiceContext
from app.store import audit
from app.store.repos import users as repo

REAUTH_FAILED = "The password is not correct."


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


def confirm_password(
    ctx: ServiceContext, actor: Actor, password: str | None, permission: str, where: str
) -> None:
    """The signed-in user's password, typed again. A wrong one is audited as a denial and counts towards
    the lockout; both are committed before the error is raised."""
    with ctx.db.transaction() as s:
        user = repo.get_user(s, actor.user_id)
        if user is None:
            raise forbidden()
        if sess.is_locked(user):
            raise locked()
        ok = pw.verify_password(user.pw_hash, password or "")
        if not ok:
            now_locked = sess.record_failure(user)
            if now_locked:
                repo.delete_user_sessions(s, user.id)
            details = {"route": where[:80], "reauth": "invalid", "locked": now_locked}
            audit.append_event(s, "access.denied", "permission", permission, details, actor.user_id)
    if not ok:
        raise locked() if now_locked else AppError(403, "reauth_invalid", REAUTH_FAILED)
