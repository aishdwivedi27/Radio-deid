"""Users, roles, sessions, settings and the minimal jobs table (SPEC §2, §7; CR-01). TR-ROLE-01..04,
TR-SEC-01.

Sessions store only sha256(token). Session times are epoch seconds (compared, never shown). Usernames are
staff logins, never patient data. ``Job`` now lives in ``jobs.py`` (Phase 4) and is re-exported here.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.store.models.base import Base
from app.store.models.jobs import Job  # moved in Phase 4; re-exported


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    pw_hash: Mapped[str] = mapped_column(String(200))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)  # locked until an admin unlocks
    is_consultant: Mapped[bool] = mapped_column(Boolean, default=False)  # TR-ROLE-03 attestation
    created_at: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(40))


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(token) hex
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    stage: Mapped[str] = mapped_column(String(20))  # password_change | active
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_ts: Mapped[int] = mapped_column(Integer)
    last_seen_ts: Mapped[int] = mapped_column(Integer)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(40))
    changed_at: Mapped[str] = mapped_column(String(32))


__all__ = ["AuthSession", "Job", "Setting", "User", "UserRole"]
