"""
/api/chat — PDF Chat with persistent sessions.

Sessions store full message history in JSONB.
Stateless per-request: client passes session_id, backend loads history from DB.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, OCRJob, JobStatus, ChatSession
from app.services.chat_service import chat_with_document, generate_suggested_questions

router = APIRouter(prefix="/chat", tags=["chat"])


# Schemas:
class StartRequest(BaseModel):
    job_id: str

class StartResponse(BaseModel):
    session_id: str
    title: str
    suggested_questions: list[str]

class AskRequest(BaseModel):
    session_id: str
    message: str

class AskResponse(BaseModel):
    answer: str
    model: str
    session_id: str

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


# Endpoints:
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

    # Generate document-specific starter questions
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


@router.post("/ask", response_model=AskResponse)
async def ask(
    data: AskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a message. Loads history from DB, appends, saves back."""
    res = await db.execute(
        select(ChatSession).where(ChatSession.id == data.session_id, ChatSession.user_id == user.id)
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")

    # Load the source document
    job_res = await db.execute(select(OCRJob).where(OCRJob.id == session.job_id))
    job = job_res.scalar_one_or_none()
    if not job or not job.result_text:
        raise HTTPException(400, "Source document no longer available")

    # Build full history including new message
    history = list(session.messages or [])
    user_msg = {"role": "user", "content": data.message}
    history.append(user_msg)

    # Track 3: pass job_id + db so chat_service uses RAG retrieval.
    # fallback_text is used only when chunks aren't ingested yet.
    result = await chat_with_document(
        job_id=session.job_id,
        messages=history,
        db=db,
        fallback_text=job.result_text,
    )

    if result["error"]:
        raise HTTPException(502, result["error"])

    assistant_msg = {"role": "assistant", "content": result["answer"]}
    history.append(assistant_msg)

    # Persist updated history
    session.messages = history
    session.updated_at = datetime.utcnow()
    db.add(session)
    await db.commit()

    return AskResponse(
        answer=result["answer"],
        model=result["model"],
        session_id=session.id,
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all chat sessions with metadata."""
    res = await db.execute(
        select(ChatSession, OCRJob.original_filename)
        .join(OCRJob, ChatSession.job_id == OCRJob.id)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
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
    res = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user.id)
    )
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "Session not found")
    await db.delete(session)
    await db.commit()