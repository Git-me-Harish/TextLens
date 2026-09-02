"""Add deleted_at for Trash (soft delete + restore)

Revision ID: 021
Revises: 020
Create Date: 2026-09-02 00:00:00

Deleting from Extraction History or Pipeline History was immediate and
irreversible — and for jobs it also deleted the source and result objects
out of MinIO, so there was nothing left to recover even in principle. One
misclick permanently destroyed a document and everything derived from it.

`deleted_at IS NULL` is now the "live" predicate for user-facing content:
  ocr_jobs, agent_runs, action_runs, chat_sessions, batch_jobs

DELETE marks the row instead of removing it, and — critically — leaves the
MinIO objects alone. Object cleanup moves to the purge path, because a
restore that hands back a row pointing at a deleted file is not a restore.

Scope is deliberately content and history only. API keys, webhooks and
connected MCP credentials keep hard delete: those are secrets and
configuration, where "deleted" has to mean *gone now*. A revoked API key
sitting recoverable in a trash can for 30 days is a security problem, not
a convenience.

Partial indexes (WHERE deleted_at IS NULL) keep the common listing query —
"my live rows, newest first" — off the trashed rows entirely, rather than
making every list scan and discard them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# table → the column its listings order by, used for the partial index
_TABLES = {
    "ocr_jobs": "created_at",
    "agent_runs": "created_at",
    "action_runs": "created_at",
    "chat_sessions": "created_at",
    "batch_jobs": "created_at",
}


def upgrade() -> None:
    for table, order_col in _TABLES.items():
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        # Covers the hot path: live rows for one user, newest first.
        op.create_index(
            f"ix_{table}_live",
            table,
            ["user_id", order_col],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )
        # Covers the trash listing and the purge sweep.
        op.create_index(
            f"ix_{table}_deleted_at",
            table,
            ["deleted_at"],
            unique=False,
            postgresql_where=sa.text("deleted_at IS NOT NULL"),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_index(f"ix_{table}_live", table_name=table)
        op.drop_column(table, "deleted_at")
