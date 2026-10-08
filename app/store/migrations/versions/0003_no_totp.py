"""remove TOTP; lockout until an admin unlocks (CR-01, Phase 3)

Plain ``ALTER TABLE`` (SQLite 3.35+) rather than a batch table rebuild, so existing users, roles and sessions
keep their foreign keys. A user locked under the old 15-minute rule starts unlocked.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08 21:06:59.307907
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_DROPPED = ("locked_until", "totp_secret", "totp_pending_secret", "totp_last_step")


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_backup_codes_user_id")
    op.execute("DROP TABLE IF EXISTS backup_codes")
    op.execute("ALTER TABLE users ADD COLUMN locked_at VARCHAR(32)")
    for column in _DROPPED:
        op.execute(f"ALTER TABLE users DROP COLUMN {column}")


def downgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN locked_until INTEGER")
    op.execute("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)")
    op.execute("ALTER TABLE users ADD COLUMN totp_pending_secret VARCHAR(64)")
    op.execute("ALTER TABLE users ADD COLUMN totp_last_step INTEGER")
    op.execute("ALTER TABLE users DROP COLUMN locked_at")
    op.execute(
        "CREATE TABLE backup_codes (id INTEGER NOT NULL PRIMARY KEY, user_id VARCHAR(40) NOT NULL "
        "REFERENCES users (id), code_hash VARCHAR(200) NOT NULL, used_at VARCHAR(32))"
    )
    op.execute("CREATE INDEX ix_backup_codes_user_id ON backup_codes (user_id)")
