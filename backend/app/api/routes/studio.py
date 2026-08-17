"""
Document Studio API routes.

Most studio operations (pdf_to_markdown, pdf_compress, pdf_split) work
with a single file and use the standard POST /jobs/upload flow from the
frontend — no new routes needed.

This router adds the two multi-file operations that can't use that flow:
  POST /studio/merge     — upload 2-10 PDFs, merge them in order
  POST /studio/combine   — upload 1-10 images, combine into one PDF

Both operations:
  1. Accept multiple file uploads
  2. Upload each to MinIO
  3. Create an OCRJob for the primary file (file_path) with extra_data
     containing the remaining object keys
  4. Dispatch process_ocr_job Celery task
  5. Return the job — frontend polls or SSE receives completion
"""

import hashlib
from typing import List

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import JobStatus, JobType, OCRJob, User
from app.schemas.schemas import JobOut
from app.services import storage_service
from app.worker.tasks import process_ocr_job

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/studio", tags=["studio"])

_MAX_FILES = 10
_MAX_BYTES = settings.MAX_FILE_SIZE_MB * 1024 * 1024


async def _upload_all(
    files: list[UploadFile],
    user_id: str,
) -> list[tuple[bytes, str, str]]:
    """
    Read + validate + upload each file to MinIO.
    Returns list of (content_bytes, object_key, original_filename).
    """
    results = []
    for f in files:
        content = await f.read()
        if not content:
            raise HTTPException(400, f"File '{f.filename}' is empty.")
        if len(content) > _MAX_BYTES:
            raise HTTPException(
                413,
                f"File '{f.filename}' exceeds {settings.MAX_FILE_SIZE_MB} MB limit.",
            )
        ct = storage_service.content_type_for(f.filename or "upload.bin")
        key = storage_service.build_upload_key(user_id, f.filename or "upload.bin")
        await storage_service.upload_bytes(content, key, ct)
        results.append((content, key, f.filename or "upload.bin"))
    return results


# Merge PDFs
@router.post("/merge", response_model=JobOut, status_code=202)
async def merge_pdfs(
    files: List[UploadFile] = File(
        ..., description="2–10 PDF files to merge in upload order"
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Merge 2–10 PDF files into a single output PDF.
    Files are merged in the order they are uploaded.

    Returns an OCRJob (job_type=pdf_merge) — monitor via SSE or GET /jobs/{id}.
    Download the merged PDF from GET /jobs/{id}/download when status=completed.
    """
    if len(files) < 2:
        raise HTTPException(400, "Provide at least 2 PDF files to merge.")
    if len(files) > _MAX_FILES:
        raise HTTPException(400, f"Maximum {_MAX_FILES} files per merge request.")

    for f in files:
        ct = storage_service.content_type_for(f.filename or "")
        if ct != "application/pdf":
            raise HTTPException(
                400, f"'{f.filename}' is not a PDF. Only PDF files can be merged."
            )

    uploaded = await _upload_all(files, user.id)

    # Primary file: first upload — object_key stored in job.file_path
    _, primary_key, primary_filename = uploaded[0]
    # Remaining: passed via extra_data so the Celery task can download them
    extra_keys = [key for _, key, _ in uploaded[1:]]

    combined_hash = hashlib.sha256(
        "".join(k for _, k, _ in uploaded).encode()
    ).hexdigest()

    job = OCRJob(
        user_id=user.id,
        job_type=JobType.pdf_merge,
        status=JobStatus.processing,
        original_filename=f"merged_{len(uploaded)}_files.pdf",
        file_path=primary_key,
        file_hash=combined_hash,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    process_ocr_job.delay(job.id, {"input_paths_keys": extra_keys})
    logger.info("studio.merge_queued", job_id=job.id[:8], file_count=len(uploaded))
    return job


# Combine images → PDF
@router.post("/combine", response_model=JobOut, status_code=202)
async def combine_images(
    files: List[UploadFile] = File(
        ..., description="1–10 image files (JPG, PNG, TIFF, WEBP)"
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Combine 1–10 images into a single PDF.
    Each image becomes one page in the output.

    Returns an OCRJob (job_type=images_to_pdf).
    """
    if not files:
        raise HTTPException(400, "Provide at least 1 image file.")
    if len(files) > _MAX_FILES:
        raise HTTPException(400, f"Maximum {_MAX_FILES} images per request.")

    _ALLOWED_IMAGE_TYPES = {
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
        "image/bmp",
    }
    for f in files:
        ct = storage_service.content_type_for(f.filename or "")
        if ct not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                400,
                f"'{f.filename}' is not a supported image type (JPG, PNG, TIFF, WEBP, BMP).",
            )

    uploaded = await _upload_all(files, user.id)
    _, primary_key, _ = uploaded[0]
    extra_keys = [key for _, key, _ in uploaded[1:]]

    job = OCRJob(
        user_id=user.id,
        job_type=JobType.images_to_pdf,
        status=JobStatus.processing,
        original_filename=f"combined_{len(uploaded)}_images.pdf",
        file_path=primary_key,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    process_ocr_job.delay(job.id, {"image_paths_keys": extra_keys})
    logger.info("studio.combine_queued", job_id=job.id[:8], image_count=len(uploaded))
    return job
