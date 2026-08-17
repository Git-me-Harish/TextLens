"""
Semantic document chunking service.

Strategy (in priority order):
  1. Split on double-newline paragraph breaks (respects natural document structure)
  2. If a paragraph exceeds MAX_CHUNK_CHARS, split on sentence endings (.!?)
  3. If a sentence still exceeds MAX_CHUNK_CHARS, hard-split on whitespace

Overlap:
  Each chunk prepends the tail of the previous chunk (OVERLAP_CHARS).
  This prevents context loss when an answer spans a chunk boundary.

Token estimation:
  Approximate: 1 token ≈ 4 characters for English text.
  Voyage AI's voyage-3 has a 32K token context limit per text — our
  target chunk size (~400 tokens ≈ 1,600 chars) is well within that.

Output per chunk:
  {
    "content":     str   — the chunk text (may include overlap prefix)
    "chunk_index": int   — 0-based position in the document
    "token_count": int   — estimated token count
    "metadata": {
      "char_start": int  — start offset in original text
      "char_end":   int  — end offset in original text
      "is_overlap": bool — True if this chunk contains overlap from previous
    }
  }
"""

import re
from typing import TypedDict

#  Tuning constants
CHARS_PER_TOKEN = 4  # English approximation
TARGET_TOKENS = 400  # ideal chunk size
MAX_TOKENS = 512  # hard cap before forced split
OVERLAP_TOKENS = 60  # tail of previous chunk re-included
MIN_TOKENS = 30  # discard chunks shorter than this

TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN  # 1600
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN  # 2048
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # 240
MIN_CHARS = MIN_TOKENS * CHARS_PER_TOKEN  # 120


class ChunkDict(TypedDict):
    content: str
    chunk_index: int
    token_count: int
    metadata: dict


#  Helpers
def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_HEADING = re.compile(r"\n#{1,6}\s+.+")  # Markdown headings
_SECTION_SEP = re.compile(r"\n{2,}")  # paragraph break


def _split_into_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def _split_paragraph_if_needed(para: str) -> list[str]:
    """
    Split a paragraph that exceeds MAX_CHARS into sentence-level pieces.
    Hard-splits any sentence still > MAX_CHARS.
    """
    if len(para) <= MAX_CHARS:
        return [para]

    sentences = _split_into_sentences(para)
    pieces: list[str] = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 <= MAX_CHARS:
            current = (current + " " + sent).strip() if current else sent
        else:
            if current:
                pieces.append(current)
            # Hard-split sentences that are themselves too large
            if len(sent) > MAX_CHARS:
                for i in range(0, len(sent), MAX_CHARS):
                    pieces.append(sent[i : i + MAX_CHARS].strip())
                current = ""
            else:
                current = sent

    if current:
        pieces.append(current)

    return pieces


#  Main function
def chunk_document(text: str) -> list[ChunkDict]:
    """
    Split `text` into overlapping semantic chunks.

    Returns an ordered list of ChunkDict — ready for embedding and DB insert.
    Empty / whitespace-only text returns [].
    """
    text = text.strip()
    if not text:
        return []

    #  1. Split on paragraph breaks
    raw_paragraphs = _SECTION_SEP.split(text)
    raw_paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

    #  2. Sub-split any oversized paragraphs
    pieces: list[str] = []
    for para in raw_paragraphs:
        pieces.extend(_split_paragraph_if_needed(para))

    #  3. Accumulate pieces into TARGET_CHARS-sized chunks
    raw_chunks: list[str] = []
    current = ""

    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + len(piece) + 2 <= TARGET_CHARS:
            current = current + "\n\n" + piece
        else:
            raw_chunks.append(current)
            current = piece

    if current:
        raw_chunks.append(current)

    #  4. Apply overlap — prepend tail of previous chunk
    result: list[ChunkDict] = []
    prev_tail = ""

    for idx, chunk_text in enumerate(raw_chunks):
        # Discard tiny trailing chunks
        if len(chunk_text) < MIN_CHARS and idx == len(raw_chunks) - 1 and result:
            # Append to the last chunk instead
            last = result[-1]
            merged = last["content"] + "\n\n" + chunk_text
            result[-1] = ChunkDict(
                content=merged,
                chunk_index=last["chunk_index"],
                token_count=_estimate_tokens(merged),
                metadata={**last["metadata"], "merged_tail": True},
            )
            break

        content_with_overlap = (
            (prev_tail + "\n\n" + chunk_text).strip() if prev_tail else chunk_text
        )
        is_overlap = bool(prev_tail)

        result.append(
            ChunkDict(
                content=content_with_overlap,
                chunk_index=idx,
                token_count=_estimate_tokens(content_with_overlap),
                metadata={
                    "is_overlap": is_overlap,
                    "raw_char_count": len(chunk_text),
                },
            )
        )

        # Tail for next chunk: last OVERLAP_CHARS of the current (pre-overlap) chunk
        prev_tail = (
            chunk_text[-OVERLAP_CHARS:].strip()
            if len(chunk_text) > OVERLAP_CHARS
            else ""
        )

    return result
