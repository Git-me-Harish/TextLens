"""Add pharmacy_orders, job_applications, accounting_entries

Revision ID: 016
Revises: 015
Create Date: 2026-08-30 00:00:00

Persisted state for the self-hosted pharmacy/job-board/accounting MCP
proxies (app/api/routes/mcp_pharmacy.py, mcp_job_board.py,
mcp_accounting.py) — real, own-backend implementations built the same
way email_api already was, since there's no actual partner account for
these three to integrate against.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pharmacy_orders",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("items", JSONB, nullable=False),
        sa.Column("total_amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("delivery_address_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="confirmed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "job_applications",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_id", sa.String(100), nullable=False),
        sa.Column("job_title", sa.String(255), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("cover_letter", sa.Text, nullable=False),
        sa.Column("applicant_name", sa.String(255), nullable=False),
        sa.Column("applicant_email", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="submitted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "accounting_entries",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("party_name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("tax_amount", sa.Float, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("entry_date", sa.String(10), nullable=False),
        sa.Column("reference_number", sa.String(100), nullable=True),
        sa.Column("account_id", sa.String(100), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("line_items", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="posted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_accounting_entries_user_type", "accounting_entries", ["user_id", "entry_type"],
    )


def downgrade() -> None:
    op.drop_table("pharmacy_orders")
    op.drop_table("job_applications")
    op.drop_index("ix_accounting_entries_user_type", table_name="accounting_entries")
    op.drop_table("accounting_entries")
