"""Extend jobtype enum with Document Studio action types

Revision ID: 004
Revises: 003
Create Date: 2025-08-01 16:00:00

Adds five new values to the jobtype PostgreSQL enum for Document Studio:
  pdf_to_markdown — convert PDF to Markdown (.md)
  pdf_merge       — combine multiple PDFs into one
  pdf_split       — extract a page range from a PDF
  pdf_compress    — reduce PDF file size
  images_to_pdf   — combine one or more images into a PDF

IMPORTANT: PostgreSQL ALTER TYPE ... ADD VALUE cannot run inside a
transaction block. We commit before adding values and re-open a
transaction after. Alembic handles the connection; we manage COMMIT/BEGIN
manually here.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUES = [
    "pdf_to_markdown",
    "pdf_merge",
    "pdf_split",
    "pdf_compress",
    "images_to_pdf",
]


def upgrade() -> None:
    # Commit the current transaction — ALTER TYPE ADD VALUE is non-transactional
    # in PostgreSQL < 14 and must run outside a transaction block.
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))

    for value in _NEW_VALUES:
        conn.execute(sa.text(f"ALTER TYPE jobtype ADD VALUE IF NOT EXISTS '{value}'"))

    # Re-open a transaction so Alembic can mark the migration as complete
    conn.execute(sa.text("BEGIN"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values directly.
    # Downgrade is a no-op — the values remain but are simply unused.
    # To fully remove them you would need to recreate the enum type.
    pass
