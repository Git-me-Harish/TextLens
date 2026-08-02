"""Initial schema — all tables and enums

Revision ID: 001
Revises:
Create Date: 2025-08-01 00:00:00.000000

Tables created (in FK dependency order):
  users → api_keys → batch_jobs → batch_items → ocr_jobs
  → agent_runs → webhooks → webhook_deliveries
  → audit_logs → field_corrections → scheduled_batches
  → chat_sessions → password_reset_tokens
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PostgreSQL native enum types

userrole_enum    = postgresql.ENUM("admin", "user",                                                                 name="userrole", create_type=False)
jobstatus_enum   = postgresql.ENUM("pending", "processing", "completed", "failed",                                 name="jobstatus", create_type=False)
jobtype_enum     = postgresql.ENUM("ocr_image", "pdf_extract", "pdf_summarize", "pdf_qa", "pdf_to_word", "image_to_pdf", name="jobtype", create_type=False)
agentstatus_enum = postgresql.ENUM("pending", "running", "completed", "failed",                                     name="agentstatus", create_type=False)
agentdomain_enum = postgresql.ENUM("finance", "healthcare", "legal", "logistics", "hr", "education", "government", "general", name="agentdomain", create_type=False)
batchstatus_enum = postgresql.ENUM("pending", "processing", "completed", "failed", "partial",                       name="batchstatus", create_type=False)


def upgrade() -> None:
    # Enums
    userrole_enum.create(op.get_bind(), checkfirst=True)
    jobstatus_enum.create(op.get_bind(), checkfirst=True)
    jobtype_enum.create(op.get_bind(), checkfirst=True)
    agentstatus_enum.create(op.get_bind(), checkfirst=True)
    agentdomain_enum.create(op.get_bind(), checkfirst=True)
    batchstatus_enum.create(op.get_bind(), checkfirst=True)

    # Users
    op.create_table(
        "users",
        sa.Column("id",              postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email",           sa.String(255), nullable=False),
        sa.Column("full_name",       sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("google_id",       sa.String(255), nullable=True),
        sa.Column("avatar_url",      sa.String(512), nullable=True),
        sa.Column("role",            userrole_enum,  nullable=False, server_default="user"),
        sa.Column("is_active",       sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("is_verified",     sa.Boolean(),   nullable=False, server_default=sa.false()),
        sa.Column("created_at",      sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",      sa.DateTime(),  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email",     "users", ["email"],     unique=True)
    op.create_index("ix_users_google_id", "users", ["google_id"], unique=True)

    # Api_keys
    op.create_table(
        "api_keys",
        sa.Column("id",             postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",        postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",           sa.String(255), nullable=False),
        sa.Column("key_prefix",     sa.String(12),  nullable=False),
        sa.Column("key_hash",       sa.String(255), nullable=False),
        sa.Column("is_active",      sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("last_used_at",   sa.DateTime(),  nullable=True),
        sa.Column("total_requests", sa.BigInteger(),nullable=False, server_default="0"),
        sa.Column("monthly_limit",  sa.Integer(),   nullable=True),
        sa.Column("created_at",     sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at",     sa.DateTime(),  nullable=True),
    )
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.create_index("ix_api_keys_key_hash",   "api_keys", ["key_hash"], unique=True)

    # Batch_jobs
    op.create_table(
        "batch_jobs",
        sa.Column("id",               postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",          postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",             sa.String(255),  nullable=False, server_default="Batch Job"),
        sa.Column("domain",           agentdomain_enum, nullable=False),
        sa.Column("pipeline_type",    sa.String(100),  nullable=False),
        sa.Column("status",           batchstatus_enum, nullable=False, server_default="pending"),
        sa.Column("total_files",      sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("completed_files",  sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("failed_files",     sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("user_instructions",sa.Text(),       nullable=True),
        sa.Column("created_at",       sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at",     sa.DateTime(),   nullable=True),
    )

    # Batch_items
    op.create_table(
        "batch_items",
        sa.Column("id",               postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("batch_job_id",     postgresql.UUID(as_uuid=False), sa.ForeignKey("batch_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename",sa.String(512), nullable=False),
        sa.Column("file_path",        sa.String(512), nullable=False),  # MinIO object key
        sa.Column("status",           batchstatus_enum, nullable=False, server_default="pending"),
        sa.Column("error_message",    sa.Text(),      nullable=True),
        sa.Column("created_at",       sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at",     sa.DateTime(),  nullable=True),
    )

    # Ocr_jobs
    # batch_item_id must come after batch_items is created.
    op.create_table(
        "ocr_jobs",
        sa.Column("id",                postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",           postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"),             nullable=False),
        sa.Column("job_type",          jobtype_enum,   nullable=False),
        sa.Column("status",            jobstatus_enum, nullable=False, server_default="pending"),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("file_path",         sa.String(512), nullable=True),   # MinIO object key
        sa.Column("result_text",       sa.Text(),      nullable=True),
        sa.Column("result_file_path",  sa.String(512), nullable=True),   # MinIO object key
        sa.Column("error_message",     sa.Text(),      nullable=True),
        sa.Column("page_count",        sa.Integer(),   nullable=True),
        sa.Column("processing_time_ms",sa.Integer(),   nullable=True),
        sa.Column("file_hash",         sa.String(64),  nullable=True),
        sa.Column("batch_item_id",     postgresql.UUID(as_uuid=False), sa.ForeignKey("batch_items.id", ondelete="SET NULL"),      nullable=True),
        sa.Column("created_at",        sa.DateTime(),  nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at",      sa.DateTime(),  nullable=True),
    )
    op.create_index("ix_ocr_jobs_file_hash", "ocr_jobs", ["file_hash"])

    # Agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("id",                postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",           postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id",     ondelete="CASCADE"),   nullable=False),
        sa.Column("ocr_job_id",        postgresql.UUID(as_uuid=False), sa.ForeignKey("ocr_jobs.id",  ondelete="SET NULL"),  nullable=True),
        sa.Column("api_key_id",        postgresql.UUID(as_uuid=False), sa.ForeignKey("api_keys.id",  ondelete="SET NULL"),  nullable=True),
        sa.Column("domain",            agentdomain_enum,  nullable=False),
        sa.Column("pipeline_type",     sa.String(100),    nullable=False),
        sa.Column("status",            agentstatus_enum,  nullable=False, server_default="pending"),
        sa.Column("input_text",        sa.Text(),         nullable=True),
        sa.Column("structured_result", postgresql.JSONB(), nullable=True),
        sa.Column("summary",           sa.Text(),         nullable=True),
        sa.Column("confidence_score",  sa.Integer(),      nullable=True),
        sa.Column("error_message",     sa.Text(),         nullable=True),
        sa.Column("processing_time_ms",sa.Integer(),      nullable=True),
        sa.Column("original_filename", sa.String(512),    nullable=True),
        sa.Column("created_at",        sa.DateTime(),     nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at",      sa.DateTime(),     nullable=True),
    )

    # Webhooks
    op.create_table(
        "webhooks",
        sa.Column("id",               postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",          postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",             sa.String(255), nullable=False),
        sa.Column("target_url",       sa.String(1024),nullable=False),
        sa.Column("secret",           sa.String(255), nullable=True),
        sa.Column("events",           postgresql.JSONB(),nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active",        sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("last_triggered_at",sa.DateTime(),  nullable=True),
        sa.Column("total_deliveries", sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("created_at",       sa.DateTime(),  nullable=False, server_default=sa.func.now()),
    )

    # Webhook_deliveries
    op.create_table(
        "webhook_deliveries",
        sa.Column("id",           postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("webhook_id",   postgresql.UUID(as_uuid=False), sa.ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event",        sa.String(64),  nullable=False),
        sa.Column("payload",      postgresql.JSONB(), nullable=False),
        sa.Column("status_code",  sa.Integer(),   nullable=True),
        sa.Column("success",      sa.Boolean(),   nullable=False, server_default=sa.false()),
        sa.Column("error_message",sa.Text(),      nullable=True),
        sa.Column("attempt",      sa.Integer(),   nullable=False, server_default="1"),
        sa.Column("created_at",   sa.DateTime(),  nullable=False, server_default=sa.func.now()),
    )

    # Audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id",          postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",     postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action",      sa.String(64),      nullable=False),
        sa.Column("entity_type", sa.String(64),      nullable=False),
        sa.Column("entity_id",   postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("extra_data",  postgresql.JSONB(), nullable=True),
        sa.Column("ip_address",  sa.String(64),      nullable=True),
        sa.Column("created_at",  sa.DateTime(),      nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_action",     "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # Field_corrections
    op.create_table(
        "field_corrections",
        sa.Column("id",              postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("agent_run_id",    postgresql.UUID(as_uuid=False), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_path",      sa.String(255), nullable=False),
        sa.Column("original_value",  sa.Text(),      nullable=True),
        sa.Column("corrected_value", sa.Text(),      nullable=False),
        sa.Column("created_at",      sa.DateTime(),  nullable=False, server_default=sa.func.now()),
    )

    # Scheduled_batches
    op.create_table(
        "scheduled_batches",
        sa.Column("id",               postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",          postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",             sa.String(255), nullable=False),
        sa.Column("cron_expr",        sa.String(64),  nullable=False),
        sa.Column("domain",           sa.String(64),  nullable=False),
        sa.Column("pipeline_type",    sa.String(100), nullable=False),
        sa.Column("drive_folder_id",  sa.String(255), nullable=True),
        sa.Column("user_instructions",sa.Text(),      nullable=True),
        sa.Column("is_active",        sa.Boolean(),   nullable=False, server_default=sa.true()),
        sa.Column("last_run_at",      sa.DateTime(),  nullable=True),
        sa.Column("next_run_at",      sa.DateTime(),  nullable=True),
        sa.Column("run_count",        sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("created_at",       sa.DateTime(),  nullable=False, server_default=sa.func.now()),
    )

    # Chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id",                  postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",             postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id",    ondelete="CASCADE"), nullable=False),
        sa.Column("job_id",              postgresql.UUID(as_uuid=False), sa.ForeignKey("ocr_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title",               sa.String(512),    nullable=False, server_default="Chat session"),
        sa.Column("messages",            postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("suggested_questions", postgresql.JSONB(), nullable=True),
        sa.Column("created_at",          sa.DateTime(),     nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(),     nullable=False, server_default=sa.func.now()),
    )

    # Password_reset_tokens
    op.create_table(
        "password_reset_tokens",
        sa.Column("id",         postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id",    postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token",      sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(),  nullable=False),
        sa.Column("used",       sa.Boolean(),   nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(),  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_password_reset_tokens_token", "password_reset_tokens", ["token"], unique=True)


def downgrade() -> None:
    op.drop_table("password_reset_tokens")
    op.drop_table("chat_sessions")
    op.drop_table("scheduled_batches")
    op.drop_table("field_corrections")
    op.drop_table("audit_logs")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_table("agent_runs")
    op.drop_table("ocr_jobs")
    op.drop_table("batch_items")
    op.drop_table("batch_jobs")
    op.drop_table("api_keys")
    op.drop_table("users")

    batchstatus_enum.drop(op.get_bind(), checkfirst=True)
    agentdomain_enum.drop(op.get_bind(), checkfirst=True)
    agentstatus_enum.drop(op.get_bind(), checkfirst=True)
    jobtype_enum.drop(op.get_bind(), checkfirst=True)
    jobstatus_enum.drop(op.get_bind(), checkfirst=True)
    userrole_enum.drop(op.get_bind(), checkfirst=True)
