"""
/api/chat — PDF Chat with persistent sessions.

Sessions store full message history in JSONB.
Stateless per-request: client passes session_id, backend loads history from DB.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import trash_service
from app.api.deps import get_current_user
from app.core.limiter import limiter
from app.db.database import get_db
from app.models.models import ChatSession, JobStatus, JobType, OCRJob, User
from app.services.chat_service import chat_with_document, generate_suggested_questions

router = APIRouter(prefix="/chat", tags=["chat"])

# Job types whose result_text is genuine document content worth chatting
# about — matches the set ingested into document_chunks by worker/tasks.py
# (_INGESTIBLE_JOB_TYPES), so "chattable" and "has real RAG chunks or will
# soon" stay in sync. pdf_qa's result_text is itself an answer, not the
# document, and every other Studio job type (merge/split/compress/etc.)
# has no meaningful body text.
_CHATTABLE_JOB_TYPES = [
    JobType.ocr_image, JobType.pdf_extract,
    JobType.pdf_to_markdown, JobType.pdf_summarize,
]


# Schemas:
class StartRequest(BaseModel):
    job_id: str


class StartResponse(BaseModel):
    session_id: str
    title: str
    suggested_questions: list[str]


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty.")
        return v


class LegacyAskRequest(BaseModel):
    """Flat-body shape for the deprecated POST /chat/ask endpoint."""
    session_id: str
    message: str = Field(..., min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty.")
        return v


class AskResponse(BaseModel):
    answer: str
    model: str
    session_id: str
    context_source: str


class SessionOut(BaseModel):
    id: str
    title: str
    job_id: str
    original_filename: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChattableDocumentOut(BaseModel):
    """Lightweight projection for the "pick an existing document" picker —
    deliberately excludes result_text so listing stays cheap even with
    many long documents."""
    id: str
    original_filename: str
    job_type: str
    page_count: int | None
    created_at: datetime
    has_chunks: bool

    class Config:
        from_attributes = True


async def _get_owned_session(db: AsyncSession, session_id: str, user_id: str) -> ChatSession:
    res = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
            ChatSession.deleted_at.is_(None),   # trashed reads as gone
        )
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    return session


async def _answer_and_persist(
    db: AsyncSession, session: ChatSession, question: str
) -> AskResponse:
    """Shared by both ask routes — loads the source doc, runs retrieval +
    the LLM call, and persists the updated message history."""
    job_res = await db.execute(select(OCRJob).where(OCRJob.id == session.job_id))
    job = job_res.scalar_one_or_none()
    if not job or not job.result_text:
        raise HTTPException(400, "Source document no longer available")

    history = list(session.messages or [])
    history.append({"role": "user", "content": question})

    result = await chat_with_document(
        job_id=session.job_id,
        messages=history,
        db=db,
        fallback_text=job.result_text,
    )
    if result["error"]:
        raise HTTPException(502, result["error"])

    history.append({"role": "assistant", "content": result["answer"]})
    session.messages = history
    session.updated_at = datetime.utcnow()
    db.add(session)
    await db.commit()

    return AskResponse(
        answer=result["answer"],
        model=result["model"],
        session_id=session.id,
        context_source=result["context_source"],
    )


# Endpoints:
@router.get("/documents", response_model=list[ChattableDocumentOut])
async def list_chattable_documents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Documents the user can start (or resume) a chat with — completed jobs
    of a type that has real body text. Backs the "use an existing PDF"
    picker so a user doesn't have to re-upload a document they already
    processed.
    """
    from app.models.models import DocumentChunk

    res = await db.execute(
        select(
            OCRJob,
            func.count(DocumentChunk.id).label("chunk_count"),
        )
        .outerjoin(DocumentChunk, DocumentChunk.job_id == OCRJob.id)
        .where(
            OCRJob.user_id == user.id,
            OCRJob.status == JobStatus.completed,
            OCRJob.job_type.in_(_CHATTABLE_JOB_TYPES),
            OCRJob.result_text.is_not(None),
        )
        .group_by(OCRJob.id)
        .order_by(OCRJob.created_at.desc())
        .limit(200)
    )
    return [
        ChattableDocumentOut(
            id=job.id,
            original_filename=job.original_filename,
            job_type=job.job_type.value,
            page_count=job.page_count,
            created_at=job.created_at,
            has_chunks=chunk_count > 0,
        )
        for job, chunk_count in res.all()
    ]


@router.post("/sessions", response_model=StartResponse, status_code=201)
async def start_session(
    data: StartRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new chat session for a document. Generates suggested questions."""
    res = await db.execute(
        select(OCRJob).where(OCRJob.id == data.job_id, OCRJob.user_id == user.id)
    )
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Document not found")
    if job.status != JobStatus.completed:
        raise HTTPException(400, "Document processing not complete")
    if not job.result_text:
        raise HTTPException(400, "Document has no extracted text")

    questions = await generate_suggested_questions(job.result_text)

    session = ChatSession(
        user_id=user.id,
        job_id=job.id,
        title=job.original_filename,
        messages=[],
        suggested_questions=questions,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return StartResponse(
        session_id=session.id,
        title=session.title,
        suggested_questions=questions,
    )


@router.post("/sessions/{session_id}/ask", response_model=AskResponse)
@limiter.limit("15/minute")
async def ask_in_session(
    request: Request,
    session_id: str,
    data: AskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Canonical send-a-message endpoint — matches the frontend's
    /chat/sessions/{id}/ask contract. Rate-limited tighter than the app
    default since every call costs a real Groq (and possibly Voyage) API
    call.
    """
    session = await _get_owned_session(db, session_id, user.id)
    return await _answer_and_persist(db, session, data.message)


@router.post("/ask", response_model=AskResponse)
@limiter.limit("15/minute")
async def ask(
    request: Request,
    data: LegacyAskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Legacy flat-body variant ({session_id, message}) — kept for any
    existing callers. New code should use POST /chat/sessions/{id}/ask.
    """
    session = await _get_owned_session(db, data.session_id, user.id)
    return await _answer_and_persist(db, session, data.message)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List the user's chat sessions with metadata, newest-updated first."""
    res = await db.execute(
        select(ChatSession, OCRJob.original_filename)
        .join(OCRJob, ChatSession.job_id == OCRJob.id)
        .where(
            ChatSession.user_id == user.id,
            ChatSession.deleted_at.is_(None),
            # A session whose document was trashed has nothing to chat about,
            # so hide it too rather than listing a link that 404s on open.
            OCRJob.deleted_at.is_(None),
        )
        .order_by(ChatSession.updated_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    rows = res.all()
    return [
        SessionOut(
            id=s.id,
            title=s.title,
            job_id=s.job_id,
            original_filename=filename,
            message_count=len(s.messages or []),
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s, filename in rows
    ]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Load a full session with all messages — for resuming."""
    res = await db.execute(
        select(ChatSession, OCRJob.original_filename)
        .join(OCRJob, ChatSession.job_id == OCRJob.id)
        .where(ChatSession.id == session_id, ChatSession.user_id == user.id)
    )
    row = res.first()
    if not row:
        raise HTTPException(404, "Session not found")
    session, filename = row
    return {
        "id": session.id,
        "title": session.title,
        "job_id": session.job_id,
        "original_filename": filename,
        "messages": session.messages or [],
        "suggested_questions": session.suggested_questions or [],
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_owned_session(db, session_id, user.id)
    # Soft delete — recoverable from Trash for 30 days (trash_service.py).
    await trash_service.soft_delete(db, "chat_session", session_id, user.id)
