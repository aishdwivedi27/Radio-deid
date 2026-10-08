"""Server-side sessions and per-user lockout (SPEC §7). TR-SEC-01.

The cookie carries a random token; the DB keeps only sha256(token). A session expires after 15 minutes
idle or 8 hours in total. A new token is issued at every stage change (login, TOTP, password change,
enrolment), so a token seen before login is never the one used after it. Lockout is per user (D-023):
5 failures lock the account for 15 minutes.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.store.models.auth import AuthSession, User
from app.store.repos import users as repo

IDLE_SECONDS = 15 * 60
ABSOLUTE_SECONDS = 8 * 60 * 60
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60

# Session stages: what a session may do next. Only "active" reaches permission-checked routes.
MFA_PENDING, PASSWORD_CHANGE, TOTP_ENROL, ACTIVE = "mfa_pending", "password_change", "totp_enrol", "active"
STAGES = (MFA_PENDING, PASSWORD_CHANGE, TOTP_ENROL, ACTIVE)


def now() -> int:
    """The clock for sessions and lockout (tests patch this)."""
    return int(time.time())


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionInfo:
    """A live session as the API sees it (no ORM object crosses the service boundary)."""

    id: str
    user_id: str
    stage: str
    csrf_token: str
    roles: frozenset[str]
    username: str


@dataclass(frozen=True)
class Issued:
    token: str  # goes into the cookie only
    csrf_token: str
    stage: str


def issue(s: Session, user_id: str, stage: str, replaces: str | None = None) -> Issued:
    """Create a session (deleting ``replaces``, the session ID being rotated)."""
    if replaces:
        repo.delete_session(s, replaces)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    t = now()
    repo.add_session(
        s,
        AuthSession(
            id=token_id(token), user_id=user_id, stage=stage, csrf_token=csrf, created_ts=t, last_seen_ts=t
        ),
    )
    return Issued(token, csrf, stage)


def resolve(s: Session, token: str | None) -> AuthSession | None:
    """The live session for ``token`` (touching ``last_seen``), or None. Expired sessions are deleted."""
    if not token:
        return None
    row = repo.get_session(s, token_id(token))
    if row is None:
        return None
    t = now()
    if t - row.last_seen_ts > IDLE_SECONDS or t - row.created_ts > ABSOLUTE_SECONDS:
        repo.delete_session(s, row.id)
        return None
    user = repo.get_user(s, row.user_id)
    if user is None or user.disabled:
        repo.delete_session(s, row.id)
        return None
    row.last_seen_ts = t
    return row


def is_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > now()


def record_failure(user: User) -> bool:
    """Count a failed password or TOTP code; returns True if the account is now locked."""
    if user.locked_until is not None and user.locked_until <= now():
        user.failed_logins, user.locked_until = 0, None  # an expired lock starts a fresh count
    user.failed_logins += 1
    if user.failed_logins >= MAX_FAILURES:
        user.locked_until = now() + LOCK_SECONDS
        return True
    return False


def clear_failures(user: User) -> None:
    user.failed_logins, user.locked_until = 0, None
