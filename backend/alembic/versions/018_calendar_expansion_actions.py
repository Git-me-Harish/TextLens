"""Add schedule_interview, track_filing_deadlines, schedule_study_sessions

Revision ID: 018
Revises: 017
Create Date: 2026-08-31 00:00:00

Expands the google_calendar integration (already OAuth-tested, working, and
so far only used by healthcare's book_appointment and legal's
track_obligations) into three more domains that have an obvious calendar
moment they were previously just leaving on the table:
  - Career:    an interview gets prepped for but never actually scheduled
  - Government: filing/permit deadlines get extracted but never tracked
  - Education: a learning plan gets generated but its sessions never
               get blocked out

Icon names are lucide-react component names — see migration 008 and
frontend/src/lib/actionIcons.jsx.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_ACTIONS = [
    # (domain, action_type, label, description, requires_credentials, icon, sort_order)
    (
        "hr", "schedule_interview",
        "Schedule Interview",
        "Check your calendar and book the interview — pass the date/time in your instructions if you have one.",
        '["google_calendar"]', "CalendarClock", 5,
    ),
    (
        "government", "track_filing_deadlines",
        "Track Filing Deadlines",
        "Create calendar reminders for permit expirations, renewal deadlines, and other required-action dates found in the document.",
        '["google_calendar"]', "CalendarClock", 4,
    ),
    (
        "education", "schedule_study_sessions",
        "Schedule Study Sessions",
        "Turn the learning plan's milestones into blocked study time on your calendar.",
        '["google_calendar"]', "Calendar", 6,
    ),
]

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
    for domain, action_type, label, description, requires_credentials, icon, sort_order in _NEW_ACTIONS:
        conn.execute(_INSERT_SQL, {
            "domain": domain, "action_type": action_type, "label": label,
            "description": description, "requires_credentials": requires_credentials,
            "icon": icon, "sort_order": sort_order,
        })


def downgrade() -> None:
    conn = op.get_bind()
    for domain, action_type, *_ in _NEW_ACTIONS:
        conn.execute(_DELETE_SQL, {"domain": domain, "action_type": action_type})
