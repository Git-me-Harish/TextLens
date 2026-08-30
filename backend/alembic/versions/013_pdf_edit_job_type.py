"""Add pdf_edit to jobtype enum — Document Studio PDF Editor

Revision ID: 013
Revises: 012
Create Date: 2026-08-30 00:00:00

The PDF Editor tool applies page reorder/rotate/delete + text overlays
client-side (pdf-lib) and then POSTs the finished PDF to
POST /studio/edit, which records it as a completed OCRJob for history/
notifications/downloads — same pattern as every other studio tool.

IMPORTANT: PostgreSQL ALTER TYPE ... ADD VALUE cannot run inside a
transaction block — same non-transactional handling as migration 004.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE jobtype ADD VALUE IF NOT EXISTS 'pdf_edit'"))
    conn.execute(sa.text("BEGIN"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly — no-op,
    # same as migration 004's downgrade.
    pass
