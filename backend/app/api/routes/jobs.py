"""
OCR jobs router — Track 1 hardened.

What changed from V1

File storage
  All file I/O goes through storage_service (MinIO).
  - Uploaded bytes → upload_bytes() → object key stored in job.file_path
  - Result files   → uploaded by Celery worker → object key in job.result_file_path
  - Downloads      → presigned URL redirect (no local disk, no FileResponse)
  - Deletions      → delete_object() / delete_objects()
  - Existence      → object_exists() replaces os.path.exists()

Job dispatch
  asyncio.ensure_future(_process_and_save(...)) → process_ocr_job.delay(job_id)
  All processing happens inside the Celery 'ocr' queue worker.

Security
  - No user-supplied paths ever reach the filesystem or storage directly.
  - All object keys come from the DB after user_id scope check.
  - Download and delete verify job ownership via DB query before acting.
"""
import hashlib
import logging
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import User, OCRJob, JobStatus, JobType
from app.schemas.schemas import JobOut, JobListResponse, QuestionRequest
from app.services import storage_service
from app.worker.tasks import process_ocr_job

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

# Constants 

ALLOWED_IMAGE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp"
})
ALLOWED_PDF_TYPE = "application/pdf"

_EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".bmp":  "image/bmp",
    ".webp": "image/webp",
}


# Helpers 
def _resolve_content_type(raw: str, filename: str) -> str:
    """
    Normalise content type — browsers sometimes send application/octet-stream
    even for PDFs/images. Fall back to extension sniffing.
    """
    if raw not in ("application/octet-stream", "", None):
        return raw
    return _EXT_TO_MIME.get(Path(filename or "").suffix.lower(), raw or "application/octet-stream")


def _allowed(content_type: str, job_type: str) -> bool:
    if job_type == JobType.ocr_image.value:
        return content_type in ALLOWED_IMAGE_TYPES
    return content_type == ALLOWED_PDF_TYPE


def _sniff_content_type(content: bytes) -> str | None:
    """
    Identify a file's real type from its magic bytes, independent of the
    client-declared Content-Type header or filename extension — both of
    which are trivially spoofable (rename a .exe to .pdf, set the header
    by hand). Only distinguishes the handful of formats this app actually
    accepts; returns None for anything else, including no signature match.
    """
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if content.startswith(b"BM"):
        return "image/bmp"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_job_type(job_type: str) -> None:
    valid = [jt.value for jt in JobType]
    if job_type not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid job_type. Choose from: {valid}")


async def _owned_job_or_404(db: AsyncSession, job_id: str, user_id: str) -> OCRJob:
    """Fetch an OCRJob that belongs to the authenticated user or raise 404."""
    row = (await db.execute(
        select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


# Routes 
@router.post("/upload", response_model=JobOut, status_code=202)
async def upload_file(
    file: UploadFile = File(...),
    job_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload a file for OCR / PDF processing.

    Flow:
      1. Validate job_type + content-type.
      2. Read bytes, enforce size limit, compute SHA-256.
      3. Reject duplicate uploads (same hash + same user).
      4. Upload bytes to MinIO — store object key in job.file_path.
      5. Create OCRJob record (status = processing).
      6. Dispatch process_ocr_job Celery task.
    """
    _validate_job_type(job_type)

    content_type = _resolve_content_type(file.content_type or "", file.filename or "")
    if not _allowed(content_type, job_type):
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' is not allowed for job_type '{job_type}'.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_FILE_SIZE_MB} MB limit.")

    # Verify the file's actual bytes, not just the client-declared header/extension —
    # both of those are spoofable and this content gets handed to PyMuPDF/Tesseract next.
    sniffed_type = _sniff_content_type(content)
    if sniffed_type is None or not _allowed(sniffed_type, job_type):
        raise HTTPException(
            status_code=400,
            detail=(
                f"File content does not match an allowed {'image' if job_type == JobType.ocr_image.value else 'PDF'} "
                f"format. The file's actual bytes were checked, not just its name or declared type."
            ),
        )
    content_type = sniffed_type  # trust the sniffed type from here on, not the client header

    file_hash = hashlib.sha256(content).hexdigest()

    # Duplicate detection — same bytes already processed by this user
    existing = (await db.execute(
        select(OCRJob)
        .where(OCRJob.user_id == user.id, OCRJob.file_hash == file_hash)
        .order_by(OCRJob.created_at.desc())
    )).scalars().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_file",
                "existing_job_id": existing.id,
                "original_filename": existing.original_filename,
                "existing_job_type": existing.job_type.value,
                "existing_job_status": existing.status.value,
            },
        )

    # Upload to MinIO — object key is the file's address from now on
    object_key = storage_service.build_upload_key(user.id, file.filename or "upload")
    await storage_service.upload_bytes(content, object_key, content_type)

    job = OCRJob(
        user_id=user.id,
        job_type=job_type,
        status=JobStatus.processing,
        original_filename=file.filename or "unknown",
        file_path=object_key,
        file_hash=file_hash,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    process_ocr_job.delay(job.id)
    logger.info("job.queued", job_id=job.id[:8], job_type=job_type, size_bytes=len(content))
    return job


@router.post("/{source_job_id}/reuse", response_model=JobOut, status_code=202)
async def reuse_source(
    source_job_id: str,
    job_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a new job from an already-uploaded file without re-uploading it.
    Both the original and the new job share the same MinIO object key.
    """
    _validate_job_type(job_type)
    source = await _owned_job_or_404(db, source_job_id, user.id)

    if not source.file_path:
        raise HTTPException(status_code=400, detail="Source job has no file associated.")

    # Validate the object is still in MinIO (not deleted)
    if not await storage_service.object_exists(source.file_path):
        raise HTTPException(status_code=404, detail="Source file is no longer available in storage.")

    job = OCRJob(
        user_id=user.id,
        job_type=job_type,
        status=JobStatus.processing,
        original_filename=source.original_filename,
        file_path=source.file_path,      # shared object key — no re-upload
        file_hash=source.file_hash,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    process_ocr_job.delay(job.id)
    logger.info("job.reuse_queued", job_id=job.id[:8], source_id=source_job_id[:8], job_type=job_type)
    return job


@router.post("/ask", response_model=JobOut)
async def ask_question(
    data: QuestionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Run a Q&A query against an already-completed OCR job.
    Creates a new pdf_qa OCRJob and dispatches it with the question in extra_data.
    """
    source = await _owned_job_or_404(db, data.job_id, user.id)

    if source.status != JobStatus.completed:
        raise HTTPException(status_code=400, detail="Source job must be completed before Q&A.")
    if not source.file_path:
        raise HTTPException(status_code=400, detail="Source job has no file associated.")

    qa_job = OCRJob(
        user_id=user.id,
        job_type=JobType.pdf_qa,
        status=JobStatus.processing,
        original_filename=source.original_filename,
        file_path=source.file_path,
        file_hash=source.file_hash,
    )
    db.add(qa_job)
    await db.flush()
    await db.refresh(qa_job)

    # Pass the question through extra_data so process_job receives it
    process_ocr_job.delay(qa_job.id, {"question": data.question})
    logger.info("job.ask_queued", job_id=qa_job.id[:8], source_id=data.job_id[:8])
    return qa_job


@router.get("", response_model=JobListResponse)
async def list_jobs(
    page: int = 1,
    per_page: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    per_page = min(per_page, 100)  # hard cap to prevent runaway queries
    offset = (page - 1) * per_page

    total = (await db.execute(
        select(func.count(OCRJob.id)).where(OCRJob.user_id == user.id)
    )).scalar()

    jobs = (await db.execute(
        select(OCRJob)
        .where(OCRJob.user_id == user.id)
        .order_by(OCRJob.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )).scalars().all()

    return JobListResponse(jobs=list(jobs), total=total, page=page, per_page=per_page)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _owned_job_or_404(db, job_id, user.id)


@router.get("/{job_id}/download")
async def download_result(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns a temporary presigned redirect URL (1 hour TTL) pointing directly
    to the result file in MinIO. The browser downloads the file from MinIO,
    not from the API server — no streaming overhead.

    Security: job ownership is verified via DB query before key is used.
    """
    job = await _owned_job_or_404(db, job_id, user.id)

    if not job.result_file_path:
        raise HTTPException(status_code=404, detail="No result file available for this job.")

    download_filename = f"textlens_{job.original_filename}"
    presigned_url = await storage_service.get_presigned_url(
        job.result_file_path,
        expires_in=3600,
        filename=download_filename,
    )

    # 302 → browser follows the presigned URL and downloads from MinIO directly
    return RedirectResponse(url=presigned_url, status_code=302)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Delete the job record.

    File cleanup logic:
      - Source file (file_path): deleted only if no other job shares the same
        object key (because /reuse jobs share the original upload).
      - Result file (result_file_path): always deleted if present — result
        files are unique per job, never shared.
    """
    job = await _owned_job_or_404(db, job_id, user.id)

    keys_to_delete: list[str] = []

    # Result file is always unique — safe to delete
    if job.result_file_path:
        keys_to_delete.append(job.result_file_path)

    # Source file may be shared across /reuse jobs — only delete when last ref
    if job.file_path:
        shared_count = (await db.execute(
            select(func.count(OCRJob.id)).where(
                OCRJob.file_path == job.file_path,
                OCRJob.id != job.id,
            )
        )).scalar() or 0

        if shared_count == 0:
            keys_to_delete.append(job.file_path)

    await db.delete(job)
    await db.flush()

    # Delete from MinIO after the DB row is gone to avoid orphaned records
    if keys_to_delete:
        await storage_service.delete_objects(keys_to_delete)

    logger.info("job.deleted", job_id=job_id[:8], objects_removed=len(keys_to_delete))


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Re-queue a failed job. The original file must still exist in MinIO.
    Resets status → processing, clears previous error/result fields.
    """
    job = await _owned_job_or_404(db, job_id, user.id)

    if job.status != JobStatus.failed:
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried.")

    if not job.file_path:
        raise HTTPException(status_code=400, detail="Job has no source file to retry.")

    if not await storage_service.object_exists(job.file_path):
        raise HTTPException(
            status_code=400,
            detail="Source file is no longer available in storage. Please upload again.",
        )

    job.status = JobStatus.processing
    job.error_message = None
    job.result_text = None
    job.result_file_path = None
    job.completed_at = None
    await db.flush()

    process_ocr_job.delay(job.id)
    logger.info("job.retry_queued", job_id=job_id[:8])
    return job