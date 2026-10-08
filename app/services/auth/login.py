"""Login, password change and logout (SPEC §7, CR-01). TR-SEC-01, TR-SEC-03.

Stages: password → (forced password change for a temporary password) → active. There is no second factor
(CR-01). Each step issues a new session token. Failed passwords count towards the per-user lockout; a locked
account stays locked until an admin unlocks it (D-032). What a user typed as a username is never audited
(it could be a password).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth import passwords as pw
from app.auth import sessions as sess
from app.auth.errors import AppError, bad_request, locked, unauthenticated
from app.services.context import ServiceContext
from app.store import audit
from app.store.models.auth import User
from app.store.repos import users as repo

BAD_LOGIN = "Invalid username or password."


@dataclass(frozen=True)
class Step:
    issued: sess.Issued
    user_id: str


def next_stage(user: User) -> str:
    return sess.PASSWORD_CHANGE if user.must_change_password else sess.ACTIVE


def fail(s: Session, user: User | None, reason: str) -> None:
    """Audit a failed sign-in; a wrong password counts towards the lockout."""
    details: dict[str, object] = {"reason": reason}
    if user is None:
        details["unknown_user"] = True
    else:
        details["locked"] = sess.record_failure(user) if reason == "password" else sess.is_locked(user)
    uid = user.id if user else None
    audit.append_event(s, "auth.login_failure", "user", uid or "-", details, uid)


def login(ctx: ServiceContext, username: str, password: str, old_session: str | None) -> Step:
    error: AppError | None = None
    with ctx.db.transaction() as s:
        user = repo.by_username(s, username or "")
        ok = pw.verify_password(user.pw_hash if user else None, password or "")
        if user is None or user.disabled:
            fail(s, user, "unknown" if user is None else "disabled")
            error = unauthenticated(BAD_LOGIN)
        elif sess.is_locked(user):
            fail(s, user, "locked")
            error = locked()
        elif not ok:
            fail(s, user, "password")
            error = locked() if sess.is_locked(user) else unauthenticated(BAD_LOGIN)
        else:
            if pw.needs_rehash(user.pw_hash):
                user.pw_hash = pw.hash_password(password)
            sess.clear_failures(user)
            audit.append_event(s, "auth.login_success", "user", user.id, {}, user.id)
            return Step(sess.issue(s, user.id, next_stage(user), old_session), user.id)
    assert error is not None
    raise error


def change_password(ctx: ServiceContext, row: sess.SessionInfo, current: str, new: str) -> Step:
    error: AppError | None = None
    with ctx.db.transaction() as s:
        user = repo.get_user(s, row.user_id)
        if user is None or row.stage not in sess.STAGES:
            raise unauthenticated()
        if not pw.verify_password(user.pw_hash, current or ""):
            fail(s, user, "password")
            if sess.is_locked(user):
                repo.delete_user_sessions(s, user.id)
            error = locked() if sess.is_locked(user) else AppError(403, "password_invalid", BAD_LOGIN)
        else:
            try:
                pw.check_policy(new or "", user.username)
            except pw.PasswordPolicyError as exc:
                raise bad_request(str(exc), "password_policy") from None
            if pw.verify_password(user.pw_hash, new):
                raise bad_request("the new password must differ from the current one", "password_policy")
            user.pw_hash, user.must_change_password = pw.hash_password(new), False
            repo.delete_user_sessions(s, user.id, keep=row.id)
            audit.append_event(s, "user.password_changed", "user", user.id, {}, user.id)
            return Step(sess.issue(s, user.id, next_stage(user), row.id), user.id)
    assert error is not None
    raise error


def logout(ctx: ServiceContext, row: sess.SessionInfo) -> None:
    with ctx.db.transaction() as s:
        repo.delete_session(s, row.id)
        audit.append_event(s, "auth.logout", "user", row.user_id, {}, row.user_id)


def current(ctx: ServiceContext, token: str | None) -> sess.SessionInfo | None:
    """The live session for the cookie token (idle and absolute limits applied, ``last_seen`` touched)."""
    if not token:
        return None
    with ctx.db.transaction() as s:
        row = sess.resolve(s, token)
        if row is None:
            return None
        user = repo.get_user(s, row.user_id)
        assert user is not None
        roles = repo.roles_of(s, user.id)
        return sess.SessionInfo(row.id, row.user_id, row.stage, row.csrf_token, roles, user.username)
