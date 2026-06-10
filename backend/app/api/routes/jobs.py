import os
import uuid
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import User, OCRJob, JobStatus, JobType
from app.schemas.schemas import JobOut, JobListResponse, QuestionRequest
from app.services.ocr_service import process_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

# Dedicated thread pool — not affected by uvicorn reload
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ocr_worker")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp"}
ALLOWED_PDF_TYPE = "application/pdf"


def _allowed(content_type: str, job_type: str) -> bool:
    if job_type == "ocr_image":
        return content_type in ALLOWED_IMAGE_TYPES
    return content_type == ALLOWED_PDF_TYPE


async def _process_and_save(job_id: str, job_type: str, file_path: str, extra: dict):
    """
    Run OCR in dedicated thread pool, then persist result.
    Uses asyncio.ensure_future so it survives outside the request lifecycle.
    """
    from app.db.database import AsyncSessionLocal

    result = {"text": None, "file_path": None, "error": None, "page_count": None, "processing_time_ms": 0}

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, process_job, job_type, file_path, extra)
        logger.info(f"[job:{job_id[:8]}] ocr done in {result.get('processing_time_ms')}ms error={result.get('error')}")
    except Exception as exc:
        logger.error(f"[job:{job_id[:8]}] executor crashed: {exc}", exc_info=True)
        result["error"] = f"{type(exc).__name__}: {exc}"

    try:
        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, job_id)
            if job:
                job.status = JobStatus.failed if result.get("error") else JobStatus.completed
                job.result_text = result.get("text")
                job.result_file_path = result.get("file_path")
                job.error_message = result.get("error")
                job.page_count = result.get("page_count")
                job.processing_time_ms = result.get("processing_time_ms")
                job.completed_at = datetime.utcnow()
                await db.commit()
                logger.info(f"[job:{job_id[:8]}] saved status={job.status.value}")
    except Exception as db_exc:
        logger.error(f"[job:{job_id[:8]}] DB write failed: {db_exc}", exc_info=True)


@router.post("/upload", response_model=JobOut, status_code=202)
async def upload_file(
    file: UploadFile = File(...),
    job_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    valid_types = [jt.value for jt in JobType]
    if job_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid job_type. Choose: {valid_types}")

    # Normalize content type — browsers sometimes send octet-stream
    content_type = file.content_type or ""
    if content_type in ("application/octet-stream", ""):
        ext = Path(file.filename or "").suffix.lower()
        ext_map = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".tiff": "image/tiff",
            ".tif": "image/tiff", ".bmp": "image/bmp",
            ".webp": "image/webp",
        }
        content_type = ext_map.get(ext, content_type)

    if not _allowed(content_type, job_type):
        raise HTTPException(status_code=400, detail=f"File type '{content_type}' not allowed for '{job_type}'")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(content) > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB}MB")

    # Save to disk
    upload_dir = Path(settings.UPLOAD_DIR) / user.id
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "file").suffix or ".bin"
    file_path = str(upload_dir / f"{uuid.uuid4()}{ext}")
    with open(file_path, "wb") as f:
        f.write(content)

    # Create job record
    job = OCRJob(
        user_id=user.id,
        job_type=job_type,
        status=JobStatus.processing,
        original_filename=file.filename or "unknown",
        file_path=file_path,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    job_id = job.id

    # Fire-and-forget using ensure_future — survives outside request lifecycle
    asyncio.ensure_future(_process_and_save(job_id, job_type, file_path, {}))
    logger.info(f"[job:{job_id[:8]}] queued type={job_type} size={len(content)}B")
    return job


@router.post("/ask", response_model=JobOut)
async def ask_question(
    data: QuestionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(select(OCRJob).where(OCRJob.id == data.job_id, OCRJob.user_id == user.id))
    source = res.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source job not found")
    if source.status != JobStatus.completed:
        raise HTTPException(status_code=400, detail="Source job must be completed first")
    if not source.file_path:
        raise HTTPException(status_code=400, detail="Source job has no file")

    qa_job = OCRJob(
        user_id=user.id,
        job_type=JobType.pdf_qa,
        status=JobStatus.processing,
        original_filename=source.original_filename,
        file_path=source.file_path,
    )
    db.add(qa_job)
    await db.flush()
    await db.refresh(qa_job)
    qa_id = qa_job.id

    asyncio.ensure_future(_process_and_save(qa_id, "pdf_qa", source.file_path, {"question": data.question}))
    return qa_job


@router.get("", response_model=JobListResponse)
async def list_jobs(
    page: int = 1,
    per_page: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    offset = (page - 1) * per_page
    total = (await db.execute(select(func.count(OCRJob.id)).where(OCRJob.user_id == user.id))).scalar()
    jobs = (await db.execute(
        select(OCRJob).where(OCRJob.user_id == user.id)
        .order_by(OCRJob.created_at.desc()).offset(offset).limit(per_page)
    )).scalars().all()
    return JobListResponse(jobs=list(jobs), total=total, page=page, per_page=per_page)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/download")
async def download_result(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = res.scalar_one_or_none()
    if not job or not job.result_file_path:
        raise HTTPException(status_code=404, detail="No result file")
    if not os.path.exists(job.result_file_path):
        raise HTTPException(status_code=404, detail="File missing from disk")
    return FileResponse(job.result_file_path, filename=f"textlens_{job.original_filename}")


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.file_path and os.path.exists(job.file_path):
        try:
            os.remove(job.file_path)
        except Exception:
            pass
    await db.delete(job)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    res = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.failed:
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")
    if not job.file_path or not os.path.exists(job.file_path):
        raise HTTPException(status_code=400, detail="Original file no longer on disk")

    job.status = JobStatus.processing
    job.error_message = None
    job.result_text = None
    job.completed_at = None
    await db.flush()

    asyncio.ensure_future(_process_and_save(job.id, job.job_type.value, job.file_path, {}))
    return job