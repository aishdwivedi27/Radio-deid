"""Users, roles, sessions and backup codes (SPEC §2, §7). TR-ROLE-01..04, TR-SEC-01."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.store.models.auth import AuthSession, User, UserRole


def count_users(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(User)) or 0


def get_user(s: Session, user_id: str) -> User | None:
    return s.get(User, user_id)


def by_username(s: Session, username: str) -> User | None:
    return s.scalars(select(User).where(func.lower(User.username) == username.strip().lower())).first()


def all_users(s: Session) -> Sequence[User]:
    return s.scalars(select(User).order_by(User.created_at, User.id)).all()


def roles_of(s: Session, user_id: str) -> frozenset[str]:
    return frozenset(s.scalars(select(UserRole.role).where(UserRole.user_id == user_id)))


def set_roles(s: Session, user_id: str, roles: Iterable[str]) -> None:
    s.execute(delete(UserRole).where(UserRole.user_id == user_id))
    s.add_all(UserRole(user_id=user_id, role=r) for r in sorted(set(roles)))
    s.flush()


def active_holders(s: Session, role: str) -> list[str]:
    """IDs of enabled users holding ``role``."""
    q = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.role == role, User.disabled.is_(False))
    )
    return list(s.scalars(q))


def add_session(s: Session, row: AuthSession) -> None:
    s.add(row)
    s.flush()


def get_session(s: Session, session_id: str) -> AuthSession | None:
    return s.get(AuthSession, session_id)


def delete_session(s: Session, session_id: str) -> None:
    s.execute(delete(AuthSession).where(AuthSession.id == session_id))


def delete_user_sessions(s: Session, user_id: str, keep: str | None = None) -> int:
    q = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep:
        q = q.where(AuthSession.id != keep)
    return s.execute(q).rowcount or 0  # type: ignore[attr-defined]
