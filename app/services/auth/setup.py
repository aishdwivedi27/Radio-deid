"""First-run setup (SPEC §7, §15.5). TR-ROLE-02, TR-SEC-01.

Steps: create the Admin and enrol TOTP; create the Custodian (a different person, attested as centre staff
and not the consultant) and enrol TOTP; the Custodian creates the key and sees its fingerprint. Then
``setup_completed_at`` is written and setup is closed for good. A setup token (cookie) ties the steps
together so nobody else can finish a half-done setup. The app then starts in pre-approval mode.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth import passwords as pw
from app.auth.errors import AppError, bad_request, conflict, forbidden, gone
from app.auth.permissions import ADMIN, CUSTODIAN
from app.deid.keys import key_fingerprint, load_or_create_key
from app.services.auth import login
from app.services.context import ServiceContext, now_iso
from app.store import audit
from app.store.models.auth import User
from app.store.repos import settings as cfg
from app.store.repos import users as repo

COMPLETED = "setup_completed_at"
TOKEN = "setup_token_sha256"  # noqa: S105 - a settings key name, not a secret
SETUP_ACTOR = "setup"


def is_complete(s: Session) -> bool:
    return cfg.get(s, COMPLETED) is not None


def _holder(s: Session, role: str) -> User | None:
    ids = repo.active_holders(s, role)
    return repo.get_user(s, ids[0]) if ids else None


@dataclass(frozen=True)
class SetupState:
    complete: bool
    next_step: str  # admin | admin_totp | custodian | custodian_totp | key | done


def state(ctx: ServiceContext) -> SetupState:
    with ctx.db.session() as s:
        if is_complete(s):
            return SetupState(True, "done")
        admin, custodian = _holder(s, ADMIN), _holder(s, CUSTODIAN)
        if admin is None:
            step = "admin"
        elif not admin.totp_secret:
            step = "admin_totp"
        elif custodian is None:
            step = "custodian"
        elif not custodian.totp_secret:
            step = "custodian_totp"
        else:
            step = "key"
        return SetupState(False, step)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_open(ctx: ServiceContext, expected: str, token: str | None) -> None:
    st = state(ctx)
    if st.complete:
        raise gone("Setup has already been completed.")
    if st.next_step != expected:
        raise conflict(f"the next setup step is {st.next_step}", "setup_step")
    if expected == "admin":
        return
    with ctx.db.session() as s:
        stored = cfg.get(s, TOKEN)
    if not token or stored is None or not secrets.compare_digest(stored, _token_hash(token)):
        raise forbidden("This setup was started in another browser session.")


def create_first(
    ctx: ServiceContext, role: str, username: str, password: str, token: str | None, attest: bool = False
) -> tuple[str, str, str, str]:
    """Create the first Admin or Custodian. Returns (setup token, user_id, TOTP secret, otpauth URI)."""
    _check_open(ctx, role, token)
    username = (username or "").strip()
    if not 3 <= len(username) <= 64:
        raise bad_request("username must be 3-64 characters")
    try:
        pw.check_policy(password or "", username)
    except pw.PasswordPolicyError as exc:
        raise bad_request(str(exc), "password_policy") from None
    if role == CUSTODIAN and not attest:
        raise bad_request("confirm that the Custodian is centre staff and not the consultant", "attestation")
    new_token = token or secrets.token_urlsafe(32)
    with ctx.db.transaction() as s:
        if repo.by_username(s, username) is not None:
            raise conflict("that username is taken (the Custodian must be a different person)")
        user = User(
            id="u_" + secrets.token_hex(4),
            username=username,
            pw_hash=pw.hash_password(password),
            must_change_password=False,
            is_consultant=False,
            created_at=now_iso(),
            created_by=SETUP_ACTOR,
        )
        s.add(user)
        s.flush()
        repo.set_roles(s, user.id, {role})
        cfg.put(s, TOKEN, _token_hash(new_token), SETUP_ACTOR, now_iso())
        audit.append_event(s, "user.created", "user", user.id, {"roles": [role], "via": "setup"}, SETUP_ACTOR)
        user_id = user.id
    secret, uri = login.start_enrolment(ctx, user_id)
    return new_token, user_id, secret, uri


def confirm_first_totp(ctx: ServiceContext, role: str, code: str, token: str | None) -> list[str]:
    _check_open(ctx, f"{role}_totp", token)
    with ctx.db.session() as s:
        user = _holder(s, role)
    if user is None:
        raise AppError(409, "setup_step", "create the account first")
    return login.confirm_enrolment(ctx, user.id, code)


def create_key(ctx: ServiceContext, token: str | None) -> str:
    """The Custodian's step: create (or adopt) the key, close setup. Returns the key fingerprint."""
    _check_open(ctx, "key", token)
    key_path = ctx.paths.key_path
    existed = key_path.exists()
    fingerprint = key_fingerprint(load_or_create_key(key_path))
    with ctx.db.transaction() as s:
        custodian = _holder(s, CUSTODIAN)
        assert custodian is not None
        if not existed:
            audit.append_event(s, "key.created", "key", fingerprint, {}, custodian.id)
        cfg.put(s, COMPLETED, now_iso(), custodian.id, now_iso())
        cfg.put(s, TOKEN, "", SETUP_ACTOR, now_iso())
        audit.append_event(s, "setup.completed", "setup", "first_run", {"key_existed": existed}, custodian.id)
    return fingerprint


def completed(ctx: ServiceContext) -> bool:
    with ctx.db.session() as s:
        return is_complete(s)
