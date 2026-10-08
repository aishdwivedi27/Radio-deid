"""Server-side sessions and per-user lockout (SPEC §7, CR-01). TR-SEC-01.

The cookie carries a random token; the DB keeps only sha256(token). A session expires after 15 minutes
idle or 8 hours in total. A new token is issued at login and at every password change, so a token seen
before login is never the one used after it. Lockout is per user (D-023): 5 consecutive failed passwords
lock the account until an admin unlocks it (CR-01, D-032); a locked user's sessions stop working.
"""

from __future__ import annotations

import datetime as dt
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

# Session stages: a temporary password must be changed before anything else.
PASSWORD_CHANGE, ACTIVE = "password_change", "active"
STAGES = (PASSWORD_CHANGE, ACTIVE)


def now() -> int:
    """The clock for sessions (tests patch this)."""
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
    """The live session for ``token`` (touching ``last_seen``), or None. Expired sessions, and sessions of
    disabled or locked users, are deleted."""
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
    if user is None or user.disabled or is_locked(user):
        repo.delete_session(s, row.id)
        return None
    row.last_seen_ts = t
    return row


def is_locked(user: User) -> bool:
    return user.locked_at is not None


def record_failure(user: User) -> bool:
    """Count a failed password; returns True if the account is (now) locked."""
    user.failed_logins += 1
    if user.failed_logins >= MAX_FAILURES and user.locked_at is None:
        user.locked_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return is_locked(user)


def clear_failures(user: User) -> None:
    """After a correct password, or when an admin unlocks the account."""
    user.failed_logins, user.locked_at = 0, None
