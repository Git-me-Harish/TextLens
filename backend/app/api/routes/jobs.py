import os
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import User, OCRJob, JobStatus, JobType
from app.schemas.schemas import JobOut, JobListResponse, QuestionRequest
from app.services.ocr_service import process_job

router = APIRouter(prefix="/jobs", tags=["jobs"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
ALLOWED_PDF_TYPE = "application/pdf"


def allowed_file(content_type: str, job_type: str) -> bool:
    if job_type == "ocr_image":
        return content_type in ALLOWED_IMAGE_TYPES
    return content_type == ALLOWED_PDF_TYPE


async def run_job_background(job_id: str, job_type: str, file_path: str, extra: dict = None):
    """Run OCR in a thread executor, then persist result."""
    from app.db.database import AsyncSessionLocal

    try:
        # Use get_running_loop() — safe inside async context
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, process_job, job_type, file_path, extra or {})
    except Exception as e:
        result = {"text": None, "file_path": None, "error": str(e), "page_count": None, "processing_time_ms": 0}

    # Always write final status back to DB
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
    except Exception as e:
        print(f"[jobs] Failed to save result for {job_id}: {e}")


@router.post("/upload", response_model=JobOut, status_code=202)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    job_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Validate job type
    if job_type not in [jt.value for jt in JobType]:
        raise HTTPException(status_code=400, detail=f"Invalid job type: {job_type}")

    # Validate file type
    if not allowed_file(file.content_type, job_type):
        raise HTTPException(status_code=400, detail="File type not allowed for this job")

    # Check file size
    content = await file.read()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit")

    # Save file to disk
    upload_dir = Path(settings.UPLOAD_DIR) / user.id
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix
    file_path = str(upload_dir / f"{file_id}{ext}")
    with open(file_path, "wb") as f:
        f.write(content)

    # Create job record
    job = OCRJob(
        user_id=user.id,
        job_type=job_type,
        status=JobStatus.processing,
        original_filename=file.filename,
        file_path=file_path,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    job_id = job.id  # capture before background task

    background_tasks.add_task(run_job_background, job_id, job_type, file_path)
    return job


@router.post("/ask", response_model=JobOut)
async def ask_question(
    background_tasks: BackgroundTasks,
    data: QuestionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ask a question against a previously processed PDF job."""
    result = await db.execute(select(OCRJob).where(OCRJob.id == data.job_id, OCRJob.user_id == user.id))
    existing_job = result.scalar_one_or_none()
    if not existing_job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not existing_job.file_path:
        raise HTTPException(status_code=400, detail="No file associated with this job")

    qa_job = OCRJob(
        user_id=user.id,
        job_type=JobType.pdf_qa,
        status=JobStatus.processing,
        original_filename=existing_job.original_filename,
        file_path=existing_job.file_path,
    )
    db.add(qa_job)
    await db.flush()
    await db.refresh(qa_job)
    qa_job_id = qa_job.id

    background_tasks.add_task(run_job_background, qa_job_id, "pdf_qa", existing_job.file_path, {"question": data.question})
    return qa_job


@router.get("", response_model=JobListResponse)
async def list_jobs(
    page: int = 1,
    per_page: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    offset = (page - 1) * per_page
    total_res = await db.execute(select(func.count(OCRJob.id)).where(OCRJob.user_id == user.id))
    total = total_res.scalar()

    jobs_res = await db.execute(
        select(OCRJob).where(OCRJob.user_id == user.id)
        .order_by(OCRJob.created_at.desc())
        .offset(offset).limit(per_page)
    )
    jobs = jobs_res.scalars().all()
    return JobListResponse(jobs=list(jobs), total=total, page=page, per_page=per_page)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/download")
async def download_result(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job or not job.result_file_path:
        raise HTTPException(status_code=404, detail="No result file available")
    if not os.path.exists(job.result_file_path):
        raise HTTPException(status_code=404, detail="Result file not found on disk")
    return FileResponse(job.result_file_path, filename=f"result_{job.original_filename}")


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.file_path and os.path.exists(job.file_path):
        os.remove(job.file_path)
    await db.delete(job)