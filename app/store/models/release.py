"""Licensees, releases, re-identification tests and breaches (SPEC §13.3-§13.7, §14).

Schema only in Phase 2; the release gate and the registers arrive in later phases.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.store.models.base import Base


class Licensee(Base):
    __tablename__ = "licensees"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))  # licensee | annotator | reid_tester
    channel: Mapped[str] = mapped_column(String(16))  # marketplace | direct | custom
    purpose: Mapped[str] = mapped_column(Text, default="")
    dac_approval_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dac_approval_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ec_notified_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    licence_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    licence_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    licence_expiry: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidentiality_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    deletion_certificate_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    licensee_id: Mapped[str] = mapped_column(ForeignKey("licensees.id"))
    export_type: Mapped[str] = mapped_column(String(16))  # release | reid_sample | catalogue
    channel: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))  # packaging | complete | failed
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    gate_report_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(32))


class ReleaseRecord(Base):
    __tablename__ = "release_records"
    __table_args__ = (UniqueConstraint("release_id", "record_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id"))
    record_id: Mapped[str] = mapped_column(ForeignKey("records.record_id"))
    record_version: Mapped[int] = mapped_column(Integer)


class ReidTest(Base):
    __tablename__ = "reid_tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    modality: Mapped[str] = mapped_column(String(4))
    release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id"), nullable=True)
    tester: Mapped[str] = mapped_column(String(200))
    tested_on: Mapped[str] = mapped_column(String(10))
    method: Mapped[str] = mapped_column(Text)
    cases_attempted: Mapped[int] = mapped_column(Integer)
    cases_reidentified: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str] = mapped_column(String(40))
    recorded_at: Mapped[str] = mapped_column(String(32))


class Breach(Base):
    __tablename__ = "breaches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discovered_at: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)  # no identifying data
    affected_json: Mapped[str] = mapped_column(Text, default="{}")  # release and licensee ids
    ec_notified_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    statutory_notes: Mapped[str] = mapped_column(Text, default="")
    closed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    releases_paused: Mapped[bool] = mapped_column(Boolean, default=False)
