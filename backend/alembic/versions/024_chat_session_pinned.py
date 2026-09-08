"""Add pinned flag to chat_sessions

Revision ID: 024
Revises: 023
Create Date: 2026-09-08 00:00:00

Chat History had no way to keep an important session at the top of the
list — it was purely sorted by updated_at, so an old but still-relevant
conversation would silently sink as newer ones came in. Paired with the
frontend's rename UI (title was previously always the source document's
filename, set once at session creation and never editable).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "pinned")
