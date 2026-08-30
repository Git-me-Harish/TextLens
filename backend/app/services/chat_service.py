"""
PDF Chat service — Track 3 RAG upgrade.

What changed from V1

Context retrieval:
  V1  — TF-IDF keyword scoring on raw result_text (re-computed per query)
  V3  — Hybrid RAG: vector search + BM25 + RRF fusion on pre-ingested chunks
         Falls back to TF-IDF if chunks not yet available (e.g. still ingesting)

Signature of chat_with_document:
  V1  — (document_text: str, messages: list) — raw text passed in
  V3  — (job_id: str, messages: list, db: AsyncSession, fallback_text: str | None)
         Fetches chunks from DB; fallback_text used only if chunks are absent.

Everything else is unchanged:
  - Groq llama-3.3-70b-versatile LLM
  - MAX_HISTORY_TURNS trimming
  - generate_suggested_questions()
  - Response shape: {answer, model, error}
"""
import json
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import rag_service

logger = structlog.get_logger(__name__)

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
# llama-3.3-70b-versatile was decommissioned from Groq's catalog (reproduced
# live: "does not exist or you do not have access to it") — every chat
# turn and every suggested-questions call was silently failing. gpt-oss-120b
# is Groq's closest current equivalent in capability. It's a reasoning
# model — its "thinking" tokens count against max_tokens and land in a
# separate `reasoning` field, not `content`; at low reasoning effort (the
# right setting for "answer from these excerpts", not a hard multi-step
# problem) this stays a small fraction of the budget, verified live.
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_REASONING_EFFORT = "low"

MAX_HISTORY_TURNS = 4     # keep last N user+assistant pairs in context
SUGGEST_EXCERPT   = 3_000 # chars fed to question-suggestion prompt

SYSTEM_TEMPLATE = """\
You are a precise document assistant. Answer questions based ONLY on the \
document excerpts provided below.

Rules:
- If the answer isn't in the excerpts, say: \
"This information isn't in the provided document sections."
- Use markdown: **bold** key values, tables for structured data, \
bullet lists for multi-part answers.
- Be thorough but concise. Lead with the direct answer.
- Do not invent excerpt numbers or citations — only reference an excerpt \
number if you are certain which one the fact came from.

--- DOCUMENT EXCERPTS (most relevant sections) ---
{context}
--- END EXCERPTS ---
"""

SUGGEST_PROMPT = """\
Analyze this document excerpt and generate exactly 3 short, specific questions \
a user would genuinely want to ask about it.
- Each question must be answerable from the document
- Under 12 words each
- No generic questions like "What is this document about?"

Document excerpt:
{excerpt}

Respond ONLY with a JSON array of 3 strings. No markdown, no preamble.
Example: ["Question one?", "Question two?", "Question three?"]
"""


# Groq call 
async def _groq_call(
    messages:    list[dict],
    max_tokens:  int   = 1024,
    temperature: float = 0.3,
) -> dict[str, Any]:
    if not settings.GROQ_API_KEY:
        return {
            "content": None,
            "error": "GROQ_API_KEY not configured. Add it to your .env file.",
        }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":            GROQ_MODEL,
                    "messages":         messages,
                    "max_tokens":       max_tokens,
                    "temperature":      temperature,
                    "reasoning_effort": GROQ_REASONING_EFFORT,
                },
            )
            data = resp.json()
            if resp.status_code != 200:
                err = data.get("error", {})
                return {"content": None, "error": err.get("message", f"Groq {resp.status_code}")}
            content = data["choices"][0]["message"]["content"]
            if not content:
                # Reasoning consumed the whole max_tokens budget before any
                # visible output — surface a real error instead of an
                # empty bubble the user can't do anything about.
                return {"content": None, "error": "The model ran out of room to answer — try a shorter question."}
            return {"content": content, "error": None}
    except Exception as exc:
        return {"content": None, "error": str(exc)}


# Public API 
async def chat_with_document(
    job_id:        str,
    messages:      list[dict[str, str]],
    db:            AsyncSession,
    fallback_text: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve relevant context and answer the latest user message.

    Args:
        job_id:        OCRJob.id — used to look up ingested chunks in pgvector.
        messages:      Full conversation history. Last entry is the current
                       user question.
        db:            Live AsyncSession — passed through to rag_service.retrieve().
        fallback_text: Raw OCR text to use if no chunks are ingested yet.
                       Prevents dead chat sessions during the ingestion window.

    Returns:
        {"answer": str, "model": str, "error": str | None, "context_source": str}
    """
    if not messages:
        return {"answer": None, "model": GROQ_MODEL, "error": "No messages provided", "context_source": "none"}

    user_query = messages[-1]["content"]

    # Retrieve context 
    chunks = await rag_service.retrieve(db, job_id, user_query)

    if chunks:
        context        = rag_service.build_context(chunks)
        context_source = "rag"
        logger.info("chat.rag_context", job_id=job_id[:8], chunks=len(chunks))
    elif fallback_text:
        # Chunks not yet ingested — fall back to TF-IDF on raw text
        context        = rag_service.retrieve_fallback(fallback_text, user_query)
        context_source = "tfidf_fallback"
        logger.info("chat.tfidf_fallback", job_id=job_id[:8], reason="no_chunks")
    else:
        context        = "No document context available."
        context_source = "none"
        logger.warning("chat.no_context", job_id=job_id[:8])

    system = SYSTEM_TEMPLATE.format(context=context)

    # Trim conversation history 
    history = [m for m in messages[:-1] if m["role"] in ("user", "assistant")]
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
    history.append(messages[-1])

    result = await _groq_call(
        [{"role": "system", "content": system}] + history,
        max_tokens=1024,
    )
    return {
        "answer":         result["content"],
        "model":          GROQ_MODEL,
        "error":          result["error"],
        "context_source": context_source,
    }


async def generate_suggested_questions(document_text: str) -> list[str]:
    """
    Generate 3 document-specific starter questions.
    Uses the first SUGGEST_EXCERPT chars — no retrieval needed.
    Unchanged from V1.
    """
    prompt = SUGGEST_PROMPT.format(excerpt=document_text[:SUGGEST_EXCERPT])
    result = await _groq_call(
        [{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.5,
    )
    if result["error"] or not result["content"]:
        return []
    try:
        raw = result["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return [q for q in json.loads(raw.strip()) if isinstance(q, str)][:3]
    except Exception:
        return []