"""Jobs, the studies each job found, and the job event stream (SPEC §4, §6.4, §6.5). TR-ING-01..05,
TR-ELIG-12..15.

Nothing here holds a path, a folder name or a file name: studies are keyed by ``study_key`` (HMAC of the
original StudyInstanceUID), folders by a key (``f01``…, ``root``) whose names live only in
``app_data/secure/jobs/<job_id>/folders.json`` (SPEC §6.4). Event payloads carry IDs and counts only.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.store.models.base import Base


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    started_by: Mapped[str] = mapped_column(String(40), index=True)
    # uploading | queued | indexing | processing | cancelling |
    # finished | reconcile_failed | cancelled | failed
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32))
    source_kind: Mapped[str] = mapped_column(String(8), default="folder")  # folder | upload
    root_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # sha256 of the input root
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    upload_files: Mapped[int] = mapped_column(Integer, default=0)
    upload_bytes: Mapped[int] = mapped_column(Integer, default=0)
    index_json: Mapped[str] = mapped_column(Text, default="{}")  # counts per folder key; no names


class JobStudy(Base):
    """One study found by a job, and what became of it (resume and reconciliation)."""

    __tablename__ = "job_studies"
    __table_args__ = (UniqueConstraint("job_id", "study_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    study_key: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str] = mapped_column(String(13))
    folder_key: Mapped[str] = mapped_column(String(8))
    spans_folders: Mapped[bool] = mapped_column(Boolean, default=False)
    n_files: Mapped[int] = mapped_column(Integer)
    # not_done | excluded | awaiting_review | auto_qa_failed | finalised | error.
    # detail_json: images_excluded, evidence, other_matches, modality (non-identifying, SPEC §6.4)
    status: Mapped[str] = mapped_column(String(20))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    with_report: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")  # no identifiers
    wrote_version: Mapped[bool] = mapped_column(Boolean, default=False)  # this job wrote the pending version
    updated_at: Mapped[str] = mapped_column(String(32))


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))
    payload_json: Mapped[str] = mapped_column(Text)
    ts: Mapped[str] = mapped_column(String(32))
