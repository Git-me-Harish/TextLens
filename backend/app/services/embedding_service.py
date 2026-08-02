"""
Voyage AI embedding service.

Why Voyage AI:
  - voyage-3 uses asymmetric embeddings: documents and queries are encoded
    differently (input_type="document" vs "query") for higher retrieval accuracy.
  - Free tier: 200M tokens/month — sufficient for production document workloads.
  - 1024-dimensional output (matches migration 002 vector column).

API shape:
  embed_documents(texts)  → list[list[float]]   (ingestion — input_type=document)
  embed_query(text)       → list[float]          (retrieval — input_type=query)
  embed_single(text)      → list[float]          (utility — no type distinction)

Batching:
  Voyage AI accepts up to 128 texts per request.
  embed_documents() automatically splits into ≤128-item batches so callers
  don't need to think about API limits.

Error handling:
  All API calls raise on HTTP errors. Callers (the ingest Celery task)
  should catch exceptions and mark the job accordingly.
"""
import asyncio
import logging
from typing import Any

import voyageai
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 128          # Voyage AI hard limit per request
_VOYAGE_MODEL = None       # lazy-resolved from settings


def _get_model() -> str:
    global _VOYAGE_MODEL
    if _VOYAGE_MODEL is None:
        _VOYAGE_MODEL = settings.VOYAGE_MODEL
    return _VOYAGE_MODEL


def _get_client() -> voyageai.AsyncClient:
    """Lazy-init async client. One per event loop is fine — voyageai uses httpx internally."""
    if not settings.VOYAGE_API_KEY:
        raise RuntimeError(
            "VOYAGE_API_KEY is not set. Add it to your .env file to enable RAG chat."
        )
    return voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY)


# Core embed calls 
async def embed_documents(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of document chunks for ingestion.

    Uses input_type="document" — Voyage AI optimises the representation
    for retrieval (slightly different from query encoding).

    Automatically batches if len(texts) > 128.
    Returns embeddings in the same order as input texts.
    """
    if not texts:
        return []

    client = _get_client()
    model  = _get_model()
    all_embeddings: list[list[float]] = []

    # Split into batches of _BATCH_SIZE
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        result = await client.embed(batch, model=model, input_type="document")
        all_embeddings.extend(result.embeddings)

    logger.debug(
        "embedding.documents_done",
        count=len(texts),
        model=model,
        dims=len(all_embeddings[0]) if all_embeddings else 0,
    )
    return all_embeddings


async def embed_query(text: str) -> list[float]:
    """
    Embed a single user query for retrieval.

    Uses input_type="query" — Voyage AI optimises for matching against
    document embeddings, giving better recall than symmetric encoding.
    """
    client = _get_client()
    model  = _get_model()
    result = await client.embed([text], model=model, input_type="query")
    logger.debug("embedding.query_done", model=model)
    return result.embeddings[0]


async def embed_single(text: str) -> list[float]:
    """
    Embed a single text with no input_type hint.
    Used for suggested question generation or utility embeddings.
    """
    client = _get_client()
    result = await client.embed([text], model=_get_model())
    return result.embeddings[0]


# Utility 
def vec_to_pg_str(embedding: list[float]) -> str:
    """
    Convert a Python list of floats to the PostgreSQL vector literal format.

    e.g. [0.1, 0.2, -0.3] → '[0.10000000,0.20000000,-0.30000000]'

    PostgreSQL casts this string literal to vector type when used with ::vector.
    """
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"