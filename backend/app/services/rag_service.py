"""
RAG retrieval service — hybrid search with Reciprocal Rank Fusion.

Pipeline

  1. embed_query(query)          → 1024-dim Voyage AI vector
  2. _vector_search(...)         → top-20 by cosine similarity (HNSW)
  3. _bm25_search(...)           → top-20 by ts_rank (GIN)
  4. _rrf_fusion(vec, bm25)      → deduplicated, RRF-ranked IDs
  5. _fetch_chunks(db, top_ids)  → ordered content dicts
  6. build_context(chunks)       → formatted string for LLM system prompt

Graceful fallback:
  If no chunks are found (document not yet ingested, or Voyage API down),
  retrieve_fallback() runs the original TF-IDF approach on raw text.

RRF formula:
  score(d) = Σ_{r ∈ rankings} 1 / (k + rank_r(d))
  k = 60  (standard value — flattens influence of top vs mid-ranked results)
"""
import hashlib
import json
import math
import re
from typing import Any

import structlog
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding_service import embed_query, vec_to_pg_str
from app.models.models import DocumentChunk
from app.db.redis import get_redis

logger = structlog.get_logger(__name__)

_RRF_K       = 60
_VECTOR_N    = 20   # candidates from vector search
_BM25_N      = 20   # candidates from BM25 search
_DEFAULT_K   = 8    # top-k returned to caller

# Retrieval (embed + vector search + BM25 + fusion) is deterministic given
# just (job_id, query) — unlike the final LLM answer, it doesn't depend on
# conversation history, so it's always safe to cache. This is what actually
# saves cost/latency on repeated or similar questions: the Voyage embedding
# call and the two DB scans, not the Groq call itself (which stays
# uncached — a follow-up question like "what about the second one?" needs
# a fresh answer even when the underlying retrieval is identical).
_CACHE_TTL_SECONDS = 3600
_CACHE_PREFIX = "rag_retrieve"


def _cache_key(job_id: str, query: str) -> str:
    normalized = " ".join(query.strip().lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"{_CACHE_PREFIX}:{job_id}:{digest}"


async def invalidate_cache(job_id: str) -> None:
    """
    Drop every cached retrieval for this document — call after (re-)ingestion
    so a manual re-index can't leave stale pre-reingest chunks being served.
    """
    try:
        redis = await get_redis()
        keys = [key async for key in redis.scan_iter(match=f"{_CACHE_PREFIX}:{job_id}:*")]
        if keys:
            await redis.delete(*keys)
            logger.info("rag.cache_invalidated", job_id=job_id[:8], keys=len(keys))
    except Exception as exc:
        logger.warning("rag.cache_invalidate_failed", job_id=job_id[:8], error=str(exc))

# Max chars to include in assembled context (≈6k tokens for Groq)
_MAX_CONTEXT_CHARS = 24_000


# Vector search 
async def _vector_search(
    db: AsyncSession,
    job_id: str,
    vec_str: str,
    limit: int = _VECTOR_N,
) -> list[tuple[str, float]]:
    """
    ANN cosine similarity search using HNSW index.
    Returns [(chunk_id, similarity_score)] ordered by similarity DESC.
    The `<=>` operator computes cosine distance; 1 - distance = similarity.
    """
    rows = await db.execute(
        text("""
            SELECT id,
                1 - (embedding <=> CAST(:vec AS vector)) AS similarity
            FROM document_chunks
            WHERE job_id = :job_id
            AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :limit
        """),
        {
            "vec": vec_str,
            "job_id": job_id,
            "limit": limit,
        },
    )
    # str() — raw SQL returns asyncpg.pgproto.pgproto.UUID objects, not
    # plain strings. _fetch_chunks() looks these ids up through the ORM,
    # where DocumentChunk.id is Mapped[str]; comparing a UUID object
    # against that column silently matched nothing (reproduced live:
    # _vector_search found the right row, but _fetch_chunks always came
    # back empty for it — every real RAG retrieval was quietly falling
    # through to the TF-IDF fallback since chunks were never fetchable).
    return [(str(row.id), float(row.similarity)) for row in rows]


# BM25 / full-text search
async def _bm25_search(
    db: AsyncSession,
    job_id: str,
    query: str,
    limit: int = _BM25_N,
) -> list[tuple[str, float]]:
    """
    PostgreSQL ts_rank BM25-style keyword search.
    plainto_tsquery normalises the query (stems, removes stop words).
    Returns [(chunk_id, rank_score)] ordered by rank DESC.
    Returns [] if no chunks have ts_vector (not yet indexed).
    """
    # Sanitise: strip characters invalid for tsquery
    clean_query = re.sub(r"[^\w\s]", " ", query).strip()
    if not clean_query:
        return []

    rows = await db.execute(
        text("""
            SELECT id,
                   ts_rank(ts_vector, plainto_tsquery('english', :query)) AS rank
            FROM   document_chunks
            WHERE  job_id    = :job_id
              AND  ts_vector @@ plainto_tsquery('english', :query)
            ORDER  BY rank DESC
            LIMIT  :limit
        """),
        {"query": clean_query, "job_id": job_id, "limit": limit},
    )
    return [(str(row.id), float(row.rank)) for row in rows]


# RRF fusion 
def _rrf_fusion(
    vector_results: list[tuple[str, float]],
    bm25_results:   list[tuple[str, float]],
    k: int = _RRF_K,
) -> list[str]:
    """
    Reciprocal Rank Fusion over two ranked lists.
    Returns chunk IDs sorted by fused score, highest first.

    A chunk in both lists scores higher than one in only one list.
    k=60 is the standard value from Cormack et al. 2009.
    """
    scores: dict[str, float] = {}

    for rank, (chunk_id, _) in enumerate(vector_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

    for rank, (chunk_id, _) in enumerate(bm25_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

    return [cid for cid, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]


# Chunk fetch 
async def _fetch_chunks(
    db: AsyncSession,
    ids: list[str],
) -> list[dict[str, Any]]:
    """
    Fetch full chunk content for a list of IDs, preserving the given order.
    Uses ORM select — no vector column needed here, so no pgvector cast required.
    """
    if not ids:
        return []

    stmt = select(DocumentChunk).where(DocumentChunk.id.in_(ids))
    result = await db.execute(stmt)
    chunk_map = {c.id: c for c in result.scalars().all()}

    # Return in the caller-supplied (RRF) order
    return [
        {
            "id":            cid,
            "content":       chunk_map[cid].content,
            "chunk_index":   chunk_map[cid].chunk_index,
            "metadata":      chunk_map[cid].chunk_metadata or {},
        }
        for cid in ids
        if cid in chunk_map
    ]


# Public retrieve 
async def retrieve(
    db: AsyncSession,
    job_id: str,
    query: str,
    top_k: int = _DEFAULT_K,
) -> list[dict[str, Any]]:
    """
    Full hybrid retrieval for a user query.

    Steps:
      1. Embed query with Voyage AI (input_type=query)
      2. ANN vector search (top 20)
      3. BM25 keyword search (top 20)
      4. RRF fusion → deduplicated ranked IDs
      5. Fetch content for top_k IDs

    Returns [] if no chunks exist (document not ingested yet).
    Callers should fall back to TF-IDF in that case.
    """
    log = logger.bind(job_id=job_id[:8], query=query[:60])

    cache_key = _cache_key(job_id, query)
    try:
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            log.info("rag.cache_hit")
            return json.loads(cached)
    except Exception as exc:
        # Cache is a pure optimization — never let a Redis hiccup break chat.
        log.warning("rag.cache_read_failed", error=str(exc))
        redis = None

    try:
        query_embedding = await embed_query(query)
        vec_str = vec_to_pg_str(query_embedding)
    except Exception as exc:
        log.error("rag.embed_query_failed", error=str(exc))
        return []

    vec_results  = await _vector_search(db, job_id, vec_str)
    bm25_results = await _bm25_search(db, job_id, query)

    if not vec_results and not bm25_results:
        log.info("rag.no_chunks_found", hint="Document may not be ingested yet")
        return []

    fused_ids = _rrf_fusion(vec_results, bm25_results)
    chunks    = await _fetch_chunks(db, fused_ids[:top_k])

    log.info(
        "rag.retrieved",
        vector_hits=len(vec_results),
        bm25_hits=len(bm25_results),
        fused_returned=len(chunks),
    )

    if chunks:
        try:
            redis = redis if redis is not None else await get_redis()
            await redis.set(cache_key, json.dumps(chunks), ex=_CACHE_TTL_SECONDS)
        except Exception as exc:
            log.warning("rag.cache_write_failed", error=str(exc))

    return chunks


# Context builder 
def build_context(chunks: list[dict[str, Any]]) -> str:
    """
    Format retrieved chunks into a context block for the LLM system prompt.

    Chunks are numbered and separated so the model can cite them.
    Total length is capped at _MAX_CONTEXT_CHARS to stay within Groq TPM limits.
    """
    parts: list[str] = []
    total = 0

    for i, chunk in enumerate(chunks, start=1):
        header   = f"[Excerpt {i}]"
        content  = chunk["content"].strip()
        block    = f"{header}\n{content}"
        block_len = len(block)

        if total + block_len > _MAX_CONTEXT_CHARS:
            # Trim the last block to fit within budget
            remaining = _MAX_CONTEXT_CHARS - total - len(header) - 1
            if remaining > 200:
                parts.append(f"{header}\n{content[:remaining]}…")
            break

        parts.append(block)
        total += block_len + 4  # 4 for the separator newlines

    return "\n\n---\n\n".join(parts)


# TF-IDF fallback 
# Preserved from V1 chat_service.py — used when document is not yet ingested.

_STOPWORDS = frozenset({
    "the","a","an","is","are","was","were","be","been","being","have","has",
    "had","do","does","did","will","would","could","should","may","might",
    "shall","can","need","dare","ought","used","what","which","who","whom",
    "this","that","these","those","i","we","you","he","she","it","they",
    "and","or","but","in","on","at","to","for","of","with","by","from","about",
})

_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')
_WORD_RE     = re.compile(r'\w+')

_FALLBACK_CHUNK_CHARS   = 6_000
_FALLBACK_OVERLAP_CHARS =   800
_FALLBACK_MAX_CHARS     = 24_000


def _fallback_split(text: str) -> list[str]:
    sentences = _SENTENCE_RE.split(text.strip())
    chunks, current, cur_len = [], [], 0
    for sent in sentences:
        sl = len(sent)
        if cur_len + sl > _FALLBACK_CHUNK_CHARS and current:
            chunks.append(" ".join(current))
            overlap, ol = [], 0
            for s in reversed(current):
                if ol + len(s) > _FALLBACK_OVERLAP_CHARS:
                    break
                overlap.insert(0, s)
                ol += len(s)
            current, cur_len = overlap, ol
        current.append(sent)
        cur_len += sl
    if current:
        chunks.append(" ".join(current))
    return chunks


def _score_chunk_tfidf(chunk: str, query: str) -> float:
    def tokens(s: str) -> list[str]:
        return [w.lower() for w in _WORD_RE.findall(s)
                if w.lower() not in _STOPWORDS and len(w) > 2]
    q_tok = tokens(query)
    if not q_tok:
        return 0.0
    c_tok = tokens(chunk)
    freq: dict[str, int] = {}
    for t in c_tok:
        freq[t] = freq.get(t, 0) + 1
    score = sum(
        math.log(1 + freq[qt]) * (2.0 if qt in chunk.lower() else 1.0)
        for qt in q_tok if qt in freq
    )
    return score / (math.log(1 + len(c_tok)) or 1)


def retrieve_fallback(text: str, query: str) -> str:
    """
    TF-IDF keyword retrieval on raw extracted text.
    Used when document chunks are not yet available in pgvector.
    """
    chunks = _fallback_split(text)
    if len(chunks) <= 3:
        return text[:_FALLBACK_MAX_CHARS]
    scored = sorted(
        enumerate(chunks),
        key=lambda x: _score_chunk_tfidf(x[1], query),
        reverse=True,
    )
    selected = sorted([i for i, _ in scored[:6]])
    # Numbered the same way as build_context() — the system prompt always
    # instructs the model to cite an excerpt number, and an unnumbered
    # fallback context led it to fabricate a plausible-looking one anyway
    # (reproduced live: "(Excerpt 3)" cited against a single, unnumbered
    # fallback block).
    parts, total = [], 0
    for n, i in enumerate(selected, start=1):
        block = chunks[i]
        if total + len(block) > _FALLBACK_MAX_CHARS:
            break
        parts.append(f"[Excerpt {n}]\n{block}")
        total += len(block)
    return "\n\n---\n\n".join(parts)