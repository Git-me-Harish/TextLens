"""Fix document_chunks.embedding dimension: 384 -> 1024

Revision ID: 015
Revises: 014
Create Date: 2026-08-30 00:10:00

Same drift pattern as 014 (chunk_metadata): migration 002's source
declares `vector(1024)` (correct for voyage-3, the configured
VOYAGE_MODEL), but the live column was actually created as
`vector(384)`. embed_documents() genuinely returns 1024-dim vectors —
reproduced live: every insert failed with
  asyncpg.exceptions.DataError: expected 384 dimensions, not 1024

document_chunks has never held a successful row (every ingest attempt
failed before reaching this point, across three separate now-fixed
bugs), so this is a safe drop-and-recreate rather than a real data
migration. The HNSW index is dropped and rebuilt against the new width.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
            dims integer;
        BEGIN
            SELECT atttypmod INTO dims
            FROM pg_attribute
            WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding';

            IF dims IS DISTINCT FROM 1024 THEN
                TRUNCATE TABLE document_chunks;
                DROP INDEX IF EXISTS ix_document_chunks_embedding;
                ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1024);
                CREATE INDEX ix_document_chunks_embedding
                ON document_chunks
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        TRUNCATE TABLE document_chunks;
        DROP INDEX IF EXISTS ix_document_chunks_embedding;
        ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(384);
        CREATE INDEX ix_document_chunks_embedding
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """)
