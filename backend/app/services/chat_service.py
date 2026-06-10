"""
PDF Chat service — Groq API (llama-3.3-70b-versatile).

Large doc handling: query-focused chunk retrieval.
- Split doc into overlapping ~1500-token chunks at sentence boundaries
- Score chunks by keyword overlap with user query (TF-IDF style)
- Send top N chunks that fit under token budget
- Keep only last 4 conversation turns
- Total payload stays under 10K tokens (safe under 12K TPM limit)
"""
import re
import json
import math
import httpx
from typing import Any

from app.core.config import settings

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Token budgets (1 token ≈ 4 chars)
CHUNK_CHAR_SIZE    = 6_000   # ~1500 tokens per chunk
CHUNK_OVERLAP_CHAR = 800     # overlap to avoid cutting context
MAX_CONTEXT_CHARS  = 24_000  # ~6000 tokens for retrieved chunks
MAX_HISTORY_TURNS  = 4       # keep last N user+assistant pairs
SUGGEST_EXCERPT    = 3_000   # chars fed to suggestion generator

SYSTEM_TEMPLATE = """\
You are a precise document assistant. Answer questions based ONLY on the document excerpts below.

Rules:
- If the answer isn't in the excerpts, say: "This information isn't in the provided document sections."
- Use markdown: **bold** key values, tables for structured data, bullet lists for multi-part answers.
- Be thorough but concise. Lead with the direct answer.

--- DOCUMENT EXCERPTS (most relevant sections) ---
{context}
--- END EXCERPTS ---
"""

SUGGEST_PROMPT = """\
Analyze this document excerpt and generate exactly 3 short, specific questions a user would genuinely want to ask about it.
- Each question must be answerable from the document
- Under 12 words each
- No generic questions like "What is this document about?"

Document excerpt:
{excerpt}

Respond ONLY with a JSON array of 3 strings. No markdown, no preamble.
Example: ["Question one?", "Question two?", "Question three?"]
"""


# ── Chunking ─────────────────────────────────────────────────────────

def _split_chunks(text: str) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries."""
    # Split on sentence-ending punctuation
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent)
        if current_len + sent_len > CHUNK_CHAR_SIZE and current:
            chunks.append(" ".join(current))
            # keep last few sentences as overlap
            overlap = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > CHUNK_OVERLAP_CHAR:
                    break
                overlap.insert(0, s)
                overlap_len += len(s)
            current = overlap
            current_len = overlap_len
        current.append(sent)
        current_len += sent_len

    if current:
        chunks.append(" ".join(current))

    return chunks


def _score_chunk(chunk: str, query: str) -> float:
    """TF-IDF-style keyword overlap score."""
    stopwords = {"the","a","an","is","are","was","were","be","been","being",
                 "have","has","had","do","does","did","will","would","could",
                 "should","may","might","shall","can","need","dare","ought",
                 "used","what","which","who","whom","this","that","these",
                 "those","i","we","you","he","she","it","they","and","or",
                 "but","in","on","at","to","for","of","with","by","from","about"}

    def tokens(s: str) -> list[str]:
        return [w.lower() for w in re.findall(r'\w+', s) if w.lower() not in stopwords and len(w) > 2]

    q_tokens = tokens(query)
    if not q_tokens:
        return 0.0

    chunk_tokens = tokens(chunk)
    chunk_freq: dict[str, int] = {}
    for t in chunk_tokens:
        chunk_freq[t] = chunk_freq.get(t, 0) + 1

    score = 0.0
    for qt in q_tokens:
        if qt in chunk_freq:
            # TF component: log(1 + freq)
            tf = math.log(1 + chunk_freq[qt])
            # Boost exact matches
            score += tf * (2.0 if qt in chunk.lower() else 1.0)

    # Normalize by chunk length to avoid long chunk bias
    return score / (math.log(1 + len(chunk_tokens)) or 1)


def _retrieve_context(text: str, query: str) -> str:
    """Return most relevant chunks joined, within token budget."""
    chunks = _split_chunks(text)

    if len(chunks) <= 3:
        # Short doc — just use it all (trimmed to budget)
        return text[:MAX_CONTEXT_CHARS]

    scored = sorted(
        enumerate(chunks),
        key=lambda x: _score_chunk(x[1], query),
        reverse=True
    )

    # Pick top chunks in original order (preserves narrative flow)
    selected_indices = sorted([i for i, _ in scored[:6]])
    selected_chunks = [chunks[i] for i in selected_indices]

    # Fit within char budget
    context = ""
    for chunk in selected_chunks:
        if len(context) + len(chunk) > MAX_CONTEXT_CHARS:
            break
        context += chunk + "\n\n---\n\n"

    return context.strip()


# ── Groq call ────────────────────────────────────────────────────────

async def _groq_call(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.3) -> dict[str, Any]:
    if not settings.GROQ_API_KEY:
        return {"content": None, "error": "GROQ_API_KEY not configured. Add it to your .env file."}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"},
                json={"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
            )
            data = resp.json()
            if resp.status_code != 200:
                err = data.get("error", {})
                return {"content": None, "error": err.get("message", f"Groq error {resp.status_code}")}
            return {"content": data["choices"][0]["message"]["content"], "error": None}
    except Exception as exc:
        return {"content": None, "error": str(exc)}


# ── Public API ───────────────────────────────────────────────────────

async def chat_with_document(
    document_text: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """
    messages = full history [{role, content}], last entry is the new user question.
    Retrieves relevant chunks, trims history to last MAX_HISTORY_TURNS turns.
    """
    user_query = messages[-1]["content"] if messages else ""

    # Retrieve relevant chunks based on the query
    context = _retrieve_context(document_text, user_query)
    system = SYSTEM_TEMPLATE.format(context=context)

    # Trim history: keep last N turns (user+assistant pairs) to save tokens
    history = [m for m in messages[:-1] if m["role"] in ("user", "assistant")]
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
    history.append(messages[-1])  # add current user message

    result = await _groq_call(
        [{"role": "system", "content": system}] + history,
        max_tokens=1024,
    )
    return {"answer": result["content"], "model": GROQ_MODEL, "error": result["error"]}


async def generate_suggested_questions(document_text: str) -> list[str]:
    """Generate 3 doc-specific starter questions from the first excerpt."""
    prompt = SUGGEST_PROMPT.format(excerpt=document_text[:SUGGEST_EXCERPT])
    result = await _groq_call(
        [{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.5,
    )
    if result["error"] or not result["content"]:
        return []
    try:
        text = result["content"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return [q for q in json.loads(text.strip()) if isinstance(q, str)][:3]
    except Exception:
        return []