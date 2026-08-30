"""
012 — Notifications table

User-facing notification feed, backing the new bell/dropdown and dashboard
panel. Distinct from audit_logs (append-only, no read state, not meant for
direct display) — this table has is_read and is meant to be queried by the
frontend directly. Populated by notification_service.py alongside the
existing job_update/agent_update SSE pushes, and a new action_update push
for the agentic action layer (which previously only published to a
per-action-run channel invisible to any global notification center).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("link", sa.String(255), nullable=True),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=False), nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    # user_id's index is already created by the column's index=True above
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    # Hot path: "give me this user's unread notifications, newest first"
    op.create_index(
        "ix_notifications_user_unread_created",
        "notifications", ["user_id", "is_read", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("notifications")
