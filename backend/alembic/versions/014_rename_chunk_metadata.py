"""Rename document_chunks.metadata to chunk_metadata

Revision ID: 014
Revises: 013
Create Date: 2026-08-30 00:00:00

Migration 002's source declares the column as `chunk_metadata`, but the
column was actually created as `metadata` on at least this environment —
the source must have been hand-edited after the migration was already
applied, without a follow-up migration to match. worker/tasks.py's
_run_ingest() INSERT uses `chunk_metadata` (matching migration 002's
current source), which was failing with UndefinedColumnError against a
live `metadata` column — reproduced live while testing.

Guarded so it's a no-op on a fresh database where migration 002 already
created the column as `chunk_metadata`.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'document_chunks' AND column_name = 'metadata'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'document_chunks' AND column_name = 'chunk_metadata'
            ) THEN
                ALTER TABLE document_chunks RENAME COLUMN metadata TO chunk_metadata;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'document_chunks' AND column_name = 'chunk_metadata'
            ) THEN
                ALTER TABLE document_chunks RENAME COLUMN chunk_metadata TO metadata;
            END IF;
        END $$;
    """)
