"""Login, second factor, password change, TOTP enrolment and logout (SPEC §7). TR-SEC-01, TR-SEC-03.

Stages: password → (TOTP or backup code) → (forced password change) → (forced TOTP enrolment for Admin and
Custodian) → active. Each step issues a new session token. Failed passwords and codes count towards the
per-user lockout (D-023). What a user typed as a username is never audited (it could be a password).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth import passwords as pw
from app.auth import sessions as sess
from app.auth import totp
from app.auth.errors import AppError, bad_request, locked, unauthenticated
from app.auth.permissions import needs_totp
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.models.auth import User
from app.store.repos import users as repo

BAD_LOGIN = "Invalid username or password."


@dataclass(frozen=True)
class Step:
    issued: sess.Issued
    user_id: str
    backup_codes: tuple[str, ...] = ()  # only right after TOTP enrolment, shown once


def next_stage(s: Session, user: User) -> str:
    if user.must_change_password:
        return sess.PASSWORD_CHANGE
    if needs_totp(repo.roles_of(s, user.id)) and not user.totp_secret:
        return sess.TOTP_ENROL
    return sess.ACTIVE


def _fail(s: Session, user: User | None, reason: str) -> None:
    details: dict[str, object] = {"reason": reason}
    if user is None:
        details["unknown_user"] = True
    else:
        counts = reason in ("password", "totp")
        details["locked"] = sess.record_failure(user) if counts else sess.is_locked(user)
    uid = user.id if user else None
    audit.append_event(s, "auth.login_failure", "user", uid or "-", details, uid)


def login(ctx: ServiceContext, username: str, password: str, old_session: str | None) -> Step:
    error: AppError | None = None
    with ctx.db.transaction() as s:
        user = repo.by_username(s, username or "")
        ok = pw.verify_password(user.pw_hash if user else None, password or "")
        if user is None or user.disabled:
            _fail(s, user, "unknown" if user is None else "disabled")
            error = unauthenticated(BAD_LOGIN)
        elif sess.is_locked(user):
            _fail(s, user, "locked")
            error = locked()
        elif not ok:
            _fail(s, user, "password")
            error = locked() if sess.is_locked(user) else unauthenticated(BAD_LOGIN)
        else:
            if pw.needs_rehash(user.pw_hash):
                user.pw_hash = pw.hash_password(password)
            if user.totp_secret:
                return Step(sess.issue(s, user.id, sess.MFA_PENDING, old_session), user.id)
            sess.clear_failures(user)
            audit.append_event(s, "auth.login_success", "user", user.id, {"mfa": "none"}, user.id)
            return Step(sess.issue(s, user.id, next_stage(s, user), old_session), user.id)
    assert error is not None
    raise error


def _use_backup_code(s: Session, user: User, code: str) -> bool:
    for row in repo.unused_backup_codes(s, user.id):
        if totp.verify_backup_code(row.code_hash, code):
            row.used_at = now_iso()
            return True
    return False


def second_factor(ctx: ServiceContext, row: sess.SessionInfo, code: str) -> Step:
    """A TOTP code or, failing that, an unused backup code."""
    error: AppError | None = None
    with ctx.db.transaction() as s:
        user = repo.get_user(s, row.user_id)
        if user is None or row.stage != sess.MFA_PENDING:
            raise unauthenticated()
        if sess.is_locked(user):
            repo.delete_session(s, row.id)
            _fail(s, user, "locked")
            error = locked()
        else:
            step = totp.matching_step(user.totp_secret or "", code or "", user.totp_last_step)
            via = "totp" if step is not None else ("backup_code" if _use_backup_code(s, user, code) else None)
            if via is None:
                _fail(s, user, "totp")
                if sess.is_locked(user):
                    repo.delete_session(s, row.id)
                error = locked() if sess.is_locked(user) else AppError(401, "totp_invalid", BAD_LOGIN)
            else:
                if step is not None:
                    user.totp_last_step = step
                else:
                    audit.append_event(s, "backup_code.used", "user", user.id, {}, user.id)
                sess.clear_failures(user)
                audit.append_event(s, "auth.login_success", "user", user.id, {"mfa": via}, user.id)
                return Step(sess.issue(s, user.id, next_stage(s, user), row.id), user.id)
    assert error is not None
    raise error


def change_password(ctx: ServiceContext, row: sess.SessionInfo, current: str, new: str) -> Step:
    error: AppError | None = None
    with ctx.db.transaction() as s:
        user = repo.get_user(s, row.user_id)
        if user is None or row.stage not in (sess.PASSWORD_CHANGE, sess.ACTIVE):
            raise unauthenticated()
        if not pw.verify_password(user.pw_hash, current or ""):
            _fail(s, user, "password")
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
            return Step(sess.issue(s, user.id, next_stage(s, user), row.id), user.id)
    assert error is not None
    raise error


def start_enrolment(ctx: ServiceContext, user_id: str, may_replace: bool = False) -> tuple[str, str]:
    """A pending TOTP secret and its otpauth:// URI (the secret is active only once confirmed)."""
    with ctx.db.transaction() as s:
        user = repo.get_user(s, user_id)
        if user is None:
            raise unauthenticated()
        if user.totp_secret and not may_replace:
            raise bad_request("an authenticator is already enrolled; an admin can reset it", "totp_enrolled")
        user.totp_pending_secret = totp.new_secret()
        return user.totp_pending_secret, totp.provisioning_uri(user.totp_pending_secret, user.username)


def confirm_enrolment(ctx: ServiceContext, user_id: str, code: str) -> list[str]:
    """Activate the pending secret with a valid code; returns 10 backup codes (stored hashed)."""
    with ctx.db.transaction() as s:
        user = repo.get_user(s, user_id)
        if user is None or not user.totp_pending_secret:
            raise bad_request("start the authenticator enrolment first", "totp_not_started")
        step = totp.matching_step(user.totp_pending_secret, code or "", None)
        if step is None:
            raise AppError(400, "totp_invalid", "The authenticator code is not valid.")
        user.totp_secret, user.totp_pending_secret, user.totp_last_step = user.totp_pending_secret, None, step
        codes = totp.new_backup_codes()
        repo.replace_backup_codes(s, user.id, [totp.hash_backup_code(c) for c in codes])
        audit.append_event(s, "totp.enrolled", "user", user.id, {"backup_codes": len(codes)}, user.id)
        return codes


def enrol_in_session(ctx: ServiceContext, row: sess.SessionInfo, code: str) -> Step:
    codes = confirm_enrolment(ctx, row.user_id, code)
    with ctx.db.transaction() as s:
        user = repo.get_user(s, row.user_id)
        assert user is not None
        repo.delete_user_sessions(s, user.id, keep=row.id)
        return Step(sess.issue(s, user.id, next_stage(s, user), row.id), user.id, tuple(codes))


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
