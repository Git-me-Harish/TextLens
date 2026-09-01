"""Seed the logistics action catalog

Revision ID: 019
Revises: 018
Create Date: 2026-09-01 00:00:00

Logistics was the one domain with document-extraction pipelines
(waybill_parser, purchase_order, customs_declaration, packing_list) but no
agentic action layer at all — no LogisticsAgent, and zero rows in
available_actions, so nothing showed up in the action picker after a
logistics document was analysed.

Every write action here reuses an MCP service that already exists and is
tested — no new integration:
  track_shipment    → google_calendar (same pattern as legal's track_obligations)
  notify_consignee  → email_api
  record_po_expense → accounting_api (the create_expense tool finance already uses)

Icon names are lucide-react component names — see migration 008 and
frontend/src/lib/actionIcons.jsx.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIONS = [
    # (action_type, label, description, requires_credentials, icon, sort_order)
    (
        "summarize_shipment",
        "Summarize Shipment",
        "Plain-language operational summary of the shipment, route, parties, and totals.",
        "[]", "FileText", 1,
    ),
    (
        "track_shipment",
        "Track Delivery Date",
        "Create a calendar reminder for the expected or required delivery date.",
        '["google_calendar"]', "CalendarClock", 2,
    ),
    (
        "notify_consignee",
        "Notify Consignee",
        "Email the consignee or buyer the tracking reference, carrier, and expected delivery.",
        '["email_api"]', "Mail", 3,
    ),
    (
        "record_po_expense",
        "Record PO in Accounting",
        "Book the purchase order into the accounting ledger as a committed expense.",
        '["accounting_api"]', "Wallet", 4,
    ),
    (
        "flag_customs_risks",
        "Flag Customs Risks",
        "Review HS codes, declared values, and missing documentation for compliance exposure.",
        "[]", "AlertTriangle", 5,
    ),
]

_INSERT_SQL = sa.text("""
    INSERT INTO available_actions
        (domain, action_type, label, description, requires_credentials, icon, sort_order)
    VALUES
        ('logistics', :action_type, :label, :description, CAST(:requires_credentials AS jsonb), :icon, :sort_order)
    ON CONFLICT (domain, action_type) DO NOTHING
""")

_DELETE_SQL = sa.text(
    "DELETE FROM available_actions WHERE domain = 'logistics' AND action_type = :action_type"
)


def upgrade() -> None:
    conn = op.get_bind()
    for action_type, label, description, requires_credentials, icon, sort_order in _ACTIONS:
        conn.execute(_INSERT_SQL, {
            "action_type": action_type, "label": label, "description": description,
            "requires_credentials": requires_credentials, "icon": icon, "sort_order": sort_order,
        })


def downgrade() -> None:
    conn = op.get_bind()
    for action_type, *_ in _ACTIONS:
        conn.execute(_DELETE_SQL, {"action_type": action_type})
