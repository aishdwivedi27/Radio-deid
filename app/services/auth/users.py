"""User administration and the Custodian grant (SPEC §2). TR-ROLE-01..04, TR-SEC-03.

Admins create users (a temporary password shown once; the user must change it at first login), disable or
re-enable them, reset passwords and assign roles other than Custodian. Only an existing Custodian grants
Custodian, with TOTP; never to the consultant's account and never to an Admin (D-025). The last active
Admin and the last active Custodian cannot be disabled or lose that role.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth import passwords as pw
from app.auth import sessions as sess
from app.auth.errors import Actor, AppError, bad_request, conflict, forbidden, not_found
from app.auth.permissions import ADMIN, CUSTODIAN, RoleError, validate_roles
from app.services.auth import access
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.models.auth import User
from app.store.repos import users as repo


@dataclass(frozen=True)
class UserView:
    id: str
    username: str
    roles: tuple[str, ...]
    disabled: bool
    locked: bool
    totp_enrolled: bool
    must_change_password: bool
    is_consultant: bool
    created_at: str

    @classmethod
    def of(cls, s: Session, u: User) -> UserView:
        return cls(
            u.id, u.username, tuple(sorted(repo.roles_of(s, u.id))), u.disabled, sess.is_locked(u),
            u.totp_secret is not None, u.must_change_password, u.is_consultant, u.created_at,
        )  # fmt: skip


def _roles(roles: list[str] | tuple[str, ...]) -> frozenset[str]:
    try:
        return validate_roles(roles)
    except RoleError as exc:
        raise bad_request(str(exc), "roles") from None


def _get(s: Session, user_id: str) -> User:
    user = repo.get_user(s, user_id)
    if user is None:
        raise not_found("No such user.")
    return user


def _protect_last(s: Session, user: User, losing: set[str]) -> None:
    for role in sorted(losing & {ADMIN, CUSTODIAN}):
        if repo.active_holders(s, role) == [user.id]:
            raise conflict(f"this is the last active {role}; add another {role} first", "last_" + role)


def list_users(ctx: ServiceContext, actor: Actor) -> list[UserView]:
    access.check(ctx, actor, "users.manage", "users.list")
    with ctx.db.session() as s:
        return [UserView.of(s, u) for u in repo.all_users(s)]


def create_user(
    ctx: ServiceContext, actor: Actor, username: str, roles: list[str], is_consultant: bool
) -> tuple[UserView, str]:
    """Returns the new user and the temporary password (shown once, never stored in clear)."""
    access.check(ctx, actor, "users.manage", "users.create")
    wanted = _roles(roles)
    if CUSTODIAN in wanted:
        raise access.deny(ctx, actor, "custodian.grant", "users.create")
    username = (username or "").strip()
    if not 3 <= len(username) <= 64 or any(c.isspace() for c in username):
        raise bad_request("username must be 3-64 characters without spaces")
    temporary = pw.generate_temporary()
    with ctx.db.transaction() as s:
        if repo.by_username(s, username) is not None:
            raise conflict("that username is taken")
        user = User(
            id="u_" + secrets.token_hex(4), username=username, pw_hash=pw.hash_password(temporary),
            must_change_password=True, is_consultant=bool(is_consultant), created_at=now_iso(),
            created_by=actor.user_id,
        )  # fmt: skip
        s.add(user)
        s.flush()
        repo.set_roles(s, user.id, wanted)
        details = {"roles": sorted(wanted), "is_consultant": bool(is_consultant)}
        audit.append_event(s, "user.created", "user", user.id, details, actor.user_id)
        return UserView.of(s, user), temporary


def set_roles(ctx: ServiceContext, actor: Actor, user_id: str, roles: list[str]) -> UserView:
    """Assign roles other than Custodian; a Custodian keeps (or lacks) that role whatever is sent."""
    access.check(ctx, actor, "users.manage", "users.roles")
    requested = set(roles)
    with ctx.db.session() as s:
        holds_custodian = CUSTODIAN in repo.roles_of(s, _get(s, user_id).id)
    if (CUSTODIAN in requested) != holds_custodian:  # audited in its own transaction, before ours
        raise access.deny(ctx, actor, "custodian.grant", "users.roles")
    wanted = _roles(sorted(requested))
    with ctx.db.transaction() as s:
        user = _get(s, user_id)
        current = set(repo.roles_of(s, user.id))
        if (CUSTODIAN in requested) != (CUSTODIAN in current):  # changed since the check above
            raise forbidden()
        _protect_last(s, user, current - wanted)
        repo.set_roles(s, user.id, wanted)
        repo.delete_user_sessions(s, user.id)
        details = {"added": sorted(wanted - current), "removed": sorted(current - wanted)}
        audit.append_event(s, "user.changed", "user", user.id, details, actor.user_id)
        return UserView.of(s, user)


def set_disabled(ctx: ServiceContext, actor: Actor, user_id: str, disabled: bool) -> UserView:
    access.check(ctx, actor, "users.manage", "users.disable")
    with ctx.db.transaction() as s:
        user = _get(s, user_id)
        if disabled and not user.disabled:
            _protect_last(s, user, set(repo.roles_of(s, user.id)))
        user.disabled = disabled
        if disabled:
            repo.delete_user_sessions(s, user.id)
        action = "user.disabled" if disabled else "user.enabled"
        audit.append_event(s, action, "user", user.id, {}, actor.user_id)
        return UserView.of(s, user)


def reset_password(ctx: ServiceContext, actor: Actor, user_id: str) -> str:
    access.check(ctx, actor, "users.manage", "users.reset_password")
    temporary = pw.generate_temporary()
    with ctx.db.transaction() as s:
        user = _get(s, user_id)
        user.pw_hash, user.must_change_password = pw.hash_password(temporary), True
        sess.clear_failures(user)
        repo.delete_user_sessions(s, user.id)
        audit.append_event(s, "user.password_reset", "user", user.id, {}, actor.user_id)
    return temporary


def grant_custodian(
    ctx: ServiceContext, actor: Actor, user_id: str, totp_code: str, attest_centre_staff: bool
) -> UserView:
    """An existing Custodian grants Custodian with a fresh TOTP code (TR-ROLE-02). The grantee must enrol
    TOTP at their next login before any Custodian action (their sessions are ended now)."""
    access.check(ctx, actor, "custodian.grant", "users.custodian")
    access.verify_step_up(ctx, actor, totp_code, "custodian.grant", "users.custodian")
    if not attest_centre_staff:
        raise bad_request("confirm that this person is centre staff and not the consultant", "attestation")
    with ctx.db.transaction() as s:
        user = _get(s, user_id)
        current = repo.roles_of(s, user.id)
        if user.is_consultant:
            raise AppError(403, "consultant", "The consultant's account can never hold Custodian.")
        if ADMIN in current:
            raise conflict("an Admin account cannot also be Custodian (different people)", "exclusive_roles")
        if user.disabled or CUSTODIAN in current:
            raise conflict("the user is disabled or already Custodian")
        repo.set_roles(s, user.id, current | {CUSTODIAN})
        repo.delete_user_sessions(s, user.id)
        details = {"attested_centre_staff": True, "totp_enrolled": user.totp_secret is not None}
        audit.append_event(s, "custodian.granted", "user", user.id, details, actor.user_id)
        return UserView.of(s, user)


def admin_reset(ctx: ServiceContext, username: str, reset_totp: bool) -> str:
    """Command-line recovery for a locked-out user (``python -m app admin-reset``). Audited as ``cli``."""
    temporary = pw.generate_temporary()
    with ctx.db.transaction() as s:
        user = repo.by_username(s, username)
        if user is None:
            raise not_found("No such user.")
        user.pw_hash, user.must_change_password = pw.hash_password(temporary), True
        sess.clear_failures(user)
        if reset_totp:
            user.totp_secret, user.totp_pending_secret, user.totp_last_step = None, None, None
            repo.replace_backup_codes(s, user.id, [])
        repo.delete_user_sessions(s, user.id)
        audit.append_event(s, "user.admin_reset", "user", user.id, {"reset_totp": reset_totp}, "cli")
    return temporary
