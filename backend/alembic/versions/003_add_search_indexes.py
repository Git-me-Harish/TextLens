"""Add full-text search GIN indexes on ocr_jobs and agent_runs

Revision ID: 003
Revises: 002
Create Date: 2025-08-01 14:00:00

Creates two functional GIN indexes using PostgreSQL's tsvector so the
search endpoint can use the @@ operator without a seq-scan on result_text.

Index strategy: expression index (no extra column) — automatically kept
in sync by PostgreSQL as rows are inserted/updated.

  ix_ocr_jobs_fts        — on to_tsvector('english', coalesce(result_text, ''))
  ix_agent_runs_fts      — on combined summary + input_text tsvector

ts_headline() is used at query time to extract highlighted excerpts;
it benefits from the index being present (cheaper candidate scan).
"""
from typing import Sequence, Union
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Full-text search on OCR job extracted text
    op.execute("""
        CREATE INDEX ix_ocr_jobs_fts
        ON ocr_jobs
        USING gin (to_tsvector('english', coalesce(result_text, '')))
    """)

    # Full-text search on agent run summary + input preview
    op.execute("""
        CREATE INDEX ix_agent_runs_fts
        ON agent_runs
        USING gin (
            to_tsvector(
                'english',
                coalesce(summary, '') || ' ' || coalesce(input_text, '')
            )
        )
    """)

    # Also index original_filename on both tables for keyword search on filenames
    op.execute("""
        CREATE INDEX ix_ocr_jobs_filename_gin
        ON ocr_jobs
        USING gin (to_tsvector('simple', coalesce(original_filename, '')))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ocr_jobs_fts")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_fts")
    op.execute("DROP INDEX IF EXISTS ix_ocr_jobs_filename_gin")