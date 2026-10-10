"""jobs extended; job_studies and job_events; one exclusions row per job and study (Phase 4)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_JOB_COLUMNS = (
    "source_kind VARCHAR(8) NOT NULL DEFAULT 'folder'",
    "root_hash VARCHAR(64)",
    "cancel_requested BOOLEAN NOT NULL DEFAULT 0",
    "started_at VARCHAR(32)",
    "finished_at VARCHAR(32)",
    "error_code VARCHAR(40)",
    "upload_files INTEGER NOT NULL DEFAULT 0",
    "upload_bytes INTEGER NOT NULL DEFAULT 0",
    "index_json TEXT NOT NULL DEFAULT '{}'",
)


def upgrade() -> None:
    for column in _JOB_COLUMNS:
        op.execute(f"ALTER TABLE jobs ADD COLUMN {column}")
    op.create_table(
        "job_studies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("study_key", sa.String(64), nullable=False),
        sa.Column("record_id", sa.String(13), nullable=False),
        sa.Column("folder_key", sa.String(8), nullable=False),
        sa.Column("spans_folders", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("n_files", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("with_report", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(40), nullable=True),
        sa.Column("rule_id", sa.String(40), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("wrote_version", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("job_id", "study_key"),
    )
    op.create_index("ix_job_studies_job_id", "job_studies", ["job_id"])
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.UniqueConstraint("job_id", "seq"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
    op.create_index("ux_exclusions_job_study", "exclusions", ["job_id", "study_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_exclusions_job_study", table_name="exclusions")
    op.drop_index("ix_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_job_studies_job_id", table_name="job_studies")
    op.drop_table("job_studies")
    for column in reversed(_JOB_COLUMNS):
        op.execute(f"ALTER TABLE jobs DROP COLUMN {column.split()[0]}")
