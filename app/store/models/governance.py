"""Ethics approval, patient lists and withdrawals (SPEC §12, §13.1). TR-COH-01..04, TR-LIST-02, TR-WDR-01..02.

Patient lists hold ``patient_code`` only; raw IDs are never stored (CLAUDE.md rule 13).
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.store.models.base import Base


class EthicsApproval(Base):
    __tablename__ = "ethics_approval"
    __table_args__ = (
        CheckConstraint("optout_window_days BETWEEN 60 AND 90", name="ck_optout_window"),
        CheckConstraint("cap_unit IN ('images', 'studies')", name="ck_cap_unit"),
        CheckConstraint("cohort_cap >= 0", name="ck_cohort_cap"),
        Index("ux_ethics_one_active", "active", unique=True, sqlite_where=text("active = 1")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    committee_name: Mapped[str] = mapped_column(String(200))
    approval_ref: Mapped[str] = mapped_column(String(80))
    protocol_version: Mapped[str] = mapped_column(String(40))
    approval_date: Mapped[str] = mapped_column(String(10))
    expiry_date: Mapped[str] = mapped_column(String(10))
    archive_start: Mapped[str] = mapped_column(String(10))
    waiver_cutoff: Mapped[str] = mapped_column(String(10))
    cohort_cap: Mapped[int] = mapped_column(Integer)
    cap_unit: Mapped[str] = mapped_column(String(8))
    notice_start: Mapped[str] = mapped_column(String(10))
    optout_window_days: Mapped[int] = mapped_column(Integer)
    legal_opinion: Mapped[bool] = mapped_column(Boolean, default=False)
    legal_opinion_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ec_amendment_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(40))


class ListVersion(Base):
    __tablename__ = "list_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_type: Mapped[str] = mapped_column(String(20))  # OPT_OUT | STAFF_VIP | MEDICO_LEGAL | CONSENT
    rows: Mapped[int] = mapped_column(Integer)
    matched: Mapped[int] = mapped_column(Integer)
    importer_id: Mapped[str] = mapped_column(String(40))
    imported_at: Mapped[str] = mapped_column(String(32))


class PatientFlag(Base):
    __tablename__ = "patient_flags"
    __table_args__ = (Index("ix_flags_patient_type", "patient_code", "flag_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_code: Mapped[str] = mapped_column(String(13))
    flag_type: Mapped[str] = mapped_column(String(20))
    list_version_id: Mapped[int] = mapped_column(ForeignKey("list_versions.id"))
    consent_value: Mapped[str | None] = mapped_column(String(3), nullable=True)  # Yes | No (CONSENT only)
    consent_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    added_at: Mapped[str] = mapped_column(String(32))


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.record_id"), unique=True)
    patient_code: Mapped[str] = mapped_column(String(13), index=True)
    reason: Mapped[str] = mapped_column(String(20))
    list_version_id: Mapped[int] = mapped_column(ForeignKey("list_versions.id"))
    withdrawn_at: Mapped[str] = mapped_column(String(32))
    row_json: Mapped[str] = mapped_column(Text)
