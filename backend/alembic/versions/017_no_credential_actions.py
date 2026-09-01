"""Clear requires_credentials for actions on now-self-hosted services

Revision ID: 017
Revises: 016
Create Date: 2026-08-30 00:20:00

pharmacy_api, job_board_api, and accounting_api are now self-hosted
against this app's own database (see mcp_pharmacy.py, mcp_job_board.py,
mcp_accounting.py, and registry.py's credential_key=None for all three) —
there's nothing left for a user to connect. Migration 005 originally
seeded available_actions.requires_credentials referencing these as
credentials the user must supply; that's now stale and would mislead any
caller of GET /actions/catalog into thinking these actions are blocked on
a missing connection.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AFFECTED_ACTION_TYPES = ("order_medicines", "find_jobs", "apply_to_job", "create_expense_entry")


def upgrade() -> None:
    op.execute(
        "UPDATE available_actions SET requires_credentials = '[]'::jsonb "
        f"WHERE action_type IN {_AFFECTED_ACTION_TYPES}"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE available_actions SET requires_credentials = '[\"pharmacy_api\"]'::jsonb "
        "WHERE action_type = 'order_medicines'"
    )
    op.execute(
        "UPDATE available_actions SET requires_credentials = '[\"job_board_api\"]'::jsonb "
        "WHERE action_type = 'find_jobs'"
    )
    op.execute(
        "UPDATE available_actions SET requires_credentials = '[\"job_board_api\", \"email_api\"]'::jsonb "
        "WHERE action_type = 'apply_to_job'"
    )
    op.execute(
        "UPDATE available_actions SET requires_credentials = '[\"accounting_api\"]'::jsonb "
        "WHERE action_type = 'create_expense_entry'"
    )
