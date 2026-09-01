"""Seed the medication-interaction and expense-anomaly actions

Revision ID: 020
Revises: 019
Create Date: 2026-09-01 00:00:00

Two additions that are pure judgement over data the platform already holds
— no new integration, no new credential.

  healthcare.check_medication_interactions
      Cross-checks a new prescription against the medicines the patient has
      actually ordered through the platform (pharmacy get_order_history),
      not against an assumed list. Read-only and no-approval: nothing leaves
      the platform and nothing is written.

  finance.flag_expense_anomalies
      Compares an invoice against the user's own posted ledger (accounting
      get_vendor_history), so "3x your usual spend with this vendor" is a
      measured claim with figures behind it rather than an assertion.

Both sit at the end of their domain's existing sort order. Icon names are
lucide-react component names — see migration 008 and
frontend/src/lib/actionIcons.jsx.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS = [
    # (domain, action_type, label, description, requires_credentials, icon, sort_order)
    (
        "healthcare",
        "check_medication_interactions",
        "Check Drug Interactions",
        "Cross-check this prescription against your existing medicines for interactions and duplication.",
        "[]", "ShieldAlert", 6,
    ),
    (
        "finance",
        "flag_expense_anomalies",
        "Flag Spending Anomalies",
        "Compare this invoice against your past spend with the vendor to surface unusual amounts and duplicates.",
        "[]", "TrendingUp", 5,
    ),
]

# requires_credentials stays empty for both: pharmacy_api and accounting_api
# are self-hosted with credential_key=None, so there is nothing for the user
# to connect and the picker must not gate these behind an integration.
_INSERT_SQL = sa.text("""
    INSERT INTO available_actions
        (domain, action_type, label, description, requires_credentials, icon, sort_order)
    VALUES
        (:domain, :action_type, :label, :description, CAST(:requires_credentials AS jsonb), :icon, :sort_order)
    ON CONFLICT (domain, action_type) DO NOTHING
""")

_DELETE_SQL = sa.text(
    "DELETE FROM available_actions WHERE domain = :domain AND action_type = :action_type"
)


def upgrade() -> None:
    conn = op.get_bind()
    for domain, action_type, label, description, requires_credentials, icon, sort_order in _ACTIONS:
        conn.execute(_INSERT_SQL, {
            "domain": domain, "action_type": action_type, "label": label,
            "description": description, "requires_credentials": requires_credentials,
            "icon": icon, "sort_order": sort_order,
        })


def downgrade() -> None:
    conn = op.get_bind()
    for domain, action_type, *_ in _ACTIONS:
        conn.execute(_DELETE_SQL, {"domain": domain, "action_type": action_type})
