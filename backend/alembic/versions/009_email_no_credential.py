"""
009 — email_api no longer requires a per-user credential

Email now sends through the platform's own Resend account
(app/api/routes/mcp_email.py), not a per-user connection — matching
registry.py's MCP_REGISTRY["email_api"] (credential_key=None). Updates the
two catalog rows that previously listed 'email_api' in requires_credentials
so the UI stops asking users to connect something that doesn't need connecting.
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

_UPDATE_SQL = sa.text(
    "UPDATE available_actions SET requires_credentials = :creds "
    "WHERE domain = :domain AND action_type = :action_type"
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(_UPDATE_SQL, {"creds": "[]", "domain": "finance", "action_type": "send_payment_reminder"})
    conn.execute(_UPDATE_SQL, {"creds": '["job_board_api"]', "domain": "hr", "action_type": "apply_to_job"})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(_UPDATE_SQL, {"creds": '["email_api"]', "domain": "finance", "action_type": "send_payment_reminder"})
    conn.execute(_UPDATE_SQL, {"creds": '["job_board_api", "email_api"]', "domain": "hr", "action_type": "apply_to_job"})
