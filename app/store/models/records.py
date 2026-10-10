"""Records, versions, images, the source-file ledger, the append log and exclusions (SPEC §4, §5).
TR-REL-NF-01, TR-ELIG-01.

Everything stored here is de-identified: ``row_json`` is exactly the line that is (or will be) appended.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.store.models.base import Base


class Record(Base):
    __tablename__ = "records"
    record_id: Mapped[str] = mapped_column(String(13), primary_key=True)
    patient_code: Mapped[str] = mapped_column(String(13), index=True)
    study_key: Mapped[str] = mapped_column(String(64), unique=True)
    latest_version: Mapped[int] = mapped_column(Integer)
    finalised_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class RecordVersion(Base):
    __tablename__ = "record_versions"
    __table_args__ = (UniqueConstraint("record_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.record_id"))
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20))
    row_json: Mapped[str] = mapped_column(Text)
    job_id: Mapped[str] = mapped_column(String(40))
    review_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    hold_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    finding_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    finalised_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (Index("ix_images_record_version", "record_id", "version"),)
    image_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.record_id"))
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(12))  # pending | finalised
    row_json: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))  # of the de-identified output file


class SourceFile(Base):
    """Ledger of source files (D-010). A file whose SHA-256, or whose SOP instance (``sop_key`` = HMAC of the
    original SOPInstanceUID), is already in a finalised version of the record is a duplicate."""

    __tablename__ = "source_files"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(13), index=True)
    version: Mapped[int] = mapped_column(Integer)
    sop_key: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(12))  # pending | finalised
    seen_at: Mapped[str] = mapped_column(String(32))


class AppendLog(Base):
    __tablename__ = "append_log"
    __table_args__ = (UniqueConstraint("file", "row_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file: Mapped[str] = mapped_column(String(32))
    row_key: Mapped[str] = mapped_column(String(40))
    line_sha256: Mapped[str] = mapped_column(String(64))
    end_offset: Mapped[int] = mapped_column(Integer)
    appended_at: Mapped[str] = mapped_column(String(32))


class Exclusion(Base):
    """One excluded study (SPEC §6.4). Hashed and coded only: no names, IDs or paths."""

    __tablename__ = "exclusions"
    __table_args__ = (Index("ux_exclusions_job_study", "job_id", "study_key", unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    study_key: Mapped[str] = mapped_column(String(64))
    source_name_hash: Mapped[str] = mapped_column(String(64))
    patient_code: Mapped[str] = mapped_column(String(13))
    reason: Mapped[str] = mapped_column(String(40))
    rule_id: Mapped[str] = mapped_column(String(40))
    rules_version: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))
