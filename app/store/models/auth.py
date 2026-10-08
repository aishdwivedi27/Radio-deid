"""Users, roles, sessions, backup codes, settings and the minimal jobs table (SPEC §2, §7). TR-ROLE-01..04,
TR-SEC-01.

Sessions store only sha256(token). Session times are epoch seconds (compared, never shown). Usernames are
staff logins, never patient data. ``jobs`` holds who started a job (separation of duties); Phase 4 extends it.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.store.models.base import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    pw_hash: Mapped[str] = mapped_column(String(200))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_pending_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int | None] = mapped_column(Integer, nullable=True)  # epoch seconds
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
    stage: Mapped[str] = mapped_column(String(20))  # mfa_pending | password_change | totp_enrol | active
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_ts: Mapped[int] = mapped_column(Integer)
    last_seen_ts: Mapped[int] = mapped_column(Integer)


class BackupCode(Base):
    __tablename__ = "backup_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(200))
    used_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(40))
    changed_at: Mapped[str] = mapped_column(String(32))


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    started_by: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32))
