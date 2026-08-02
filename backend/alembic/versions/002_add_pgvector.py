"""Add pgvector extension and document_chunks table

Revision ID: 002
Revises: 001
Create Date: 2025-08-01 12:00:00

Adds:
  - vector extension (pgvector)
  - document_chunks table with:
      embedding  vector(1024)    — Voyage AI voyage-3 output dims
      ts_vector  tsvector        — BM25 full-text search (GIN indexed)
      HNSW index on embedding    — approximate nearest-neighbour search
        m=16, ef_construction=64 (standard balanced defaults)
      GIN  index on ts_vector
      B-tree index on job_id

Why HNSW over IVFFlat:
  HNSW builds incrementally on inserts — no need to pre-load data before
  creating the index. Better recall/speed trade-off for document-scale
  workloads (<1M rows).  Requires pgvector >= 0.5.0 (pgvector/pgvector:pg16
  ships with 0.8+).
"""
from typing import Sequence, Union
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    #  pgvector extension 
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    #  document_chunks table 
    # Using raw DDL because SQLAlchemy's op.create_table() doesn't know
    # the vector type — it's a pgvector-native PostgreSQL type.
    op.execute("""
        CREATE TABLE document_chunks (
            id               UUID         PRIMARY KEY,
            job_id           UUID         NOT NULL
                             REFERENCES ocr_jobs(id) ON DELETE CASCADE,
            user_id          UUID         NOT NULL
                             REFERENCES users(id)    ON DELETE CASCADE,
            chunk_index      INTEGER      NOT NULL,
            content          TEXT         NOT NULL,
            token_count      INTEGER,
            embedding        vector(1024),
            ts_vector        tsvector,
            chunk_metadata   JSONB,
            created_at       TIMESTAMP    NOT NULL DEFAULT NOW()
        )
    """)

    #  Indexes 
    # B-tree on job_id — used in every WHERE job_id = ? filter
    op.execute("""
        CREATE INDEX ix_document_chunks_job_id
        ON document_chunks (job_id)
    """)

    # HNSW on embedding : sub-millisecond ANN cosine search
    # m=16            number of bi-directional links per node
    # ef_construction candidate list size during index build (higher = better recall, slower build)
    op.execute("""
        CREATE INDEX ix_document_chunks_embedding
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # GIN on ts_vector — BM25 keyword search
    op.execute("""
        CREATE INDEX ix_document_chunks_ts_vector
        ON document_chunks
        USING gin (ts_vector)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_chunks")
    op.execute("DROP EXTENSION IF EXISTS vector")
