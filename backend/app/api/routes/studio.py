"""
Document Studio API routes.

Most studio operations (pdf_to_markdown, pdf_compress) work with a single
file and use the standard POST /jobs/upload flow from the frontend — no
new routes needed.

This router adds the operations that don't fit that single-file, no-extra-
params contract:
  POST /studio/merge     — upload 2-10 PDFs, merge them in order
  POST /studio/combine   — upload 1-10 images, combine into one PDF
  POST /studio/split     — upload 1 PDF + a page range to extract
  POST /studio/edit      — upload an already-edited PDF (client-side pdf-lib
                            edits — page reorder/rotate/delete, text overlays)
                            and record it as a completed job for history/
                            notifications, without any further processing

Merge/combine/split:
  1. Accept file upload(s)
  2. Upload each to MinIO
  3. Create an OCRJob for the primary file (file_path) with extra_data
     containing the remaining object keys / page range
  4. Dispatch process_ocr_job Celery task
  5. Return the job — frontend polls or SSE receives completion
"""

import hashlib
from datetime import datetime
from typing import List

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import JobStatus, JobType, OCRJob, User
from app.schemas.schemas import JobOut
from app.services import storage_service
from app.services.notification_service import create_notification
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


# Split PDF — extract a page range
@router.post("/split", response_model=JobOut, status_code=202)
async def split_pdf(
    file: UploadFile = File(..., description="PDF file to split"),
    from_page: int = Form(..., ge=1, description="First page to keep (1-indexed)"),
    to_page: int = Form(..., ge=1, description="Last page to keep (1-indexed, inclusive)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Extract a page range [from_page, to_page] from a single PDF.

    Out-of-range values are clamped server-side (see ocr_service.pdf_split) —
    this route only validates that both are positive integers.

    Returns an OCRJob (job_type=pdf_split) — monitor via SSE or GET /jobs/{id}.
    """
    ct = storage_service.content_type_for(file.filename or "")
    if ct != "application/pdf":
        raise HTTPException(400, f"'{file.filename}' is not a PDF.")
    if to_page < from_page:
        raise HTTPException(400, "to_page must be greater than or equal to from_page.")

    (content, key, filename), = await _upload_all([file], user.id)
    file_hash = hashlib.sha256(content).hexdigest()

    job = OCRJob(
        user_id=user.id,
        job_type=JobType.pdf_split,
        status=JobStatus.processing,
        original_filename=filename,
        file_path=key,
        file_hash=file_hash,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    process_ocr_job.delay(job.id, {"from_page": from_page, "to_page": to_page})
    logger.info("studio.split_queued", job_id=job.id[:8], from_page=from_page, to_page=to_page)
    return job


# Save an edited PDF (client-side pdf-lib edits — page reorder/rotate/delete,
# text overlays) — the work is already done in the browser, this just
# persists the result as a completed job so it shows up in history/
# notifications/downloads consistently with every other studio tool.
@router.post("/edit", response_model=JobOut, status_code=201)
async def save_edited_pdf(
    file: UploadFile = File(..., description="The already-edited PDF"),
    original_filename: str = Form("edited.pdf"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ct = storage_service.content_type_for(file.filename or original_filename)
    if ct != "application/pdf":
        raise HTTPException(400, "Only PDF files can be saved from the editor.")

    content = await file.read()
    if not content:
        raise HTTPException(400, "The edited file is empty.")
    if len(content) > _MAX_BYTES:
        raise HTTPException(413, f"File exceeds {settings.MAX_FILE_SIZE_MB} MB limit.")

    key = storage_service.build_upload_key(user.id, original_filename)
    await storage_service.upload_bytes(content, key, "application/pdf")
    file_hash = hashlib.sha256(content).hexdigest()

    job = OCRJob(
        user_id=user.id,
        job_type=JobType.pdf_edit,
        status=JobStatus.completed,
        original_filename=original_filename,
        file_path=key,
        result_file_path=key,
        file_hash=file_hash,
        completed_at=datetime.utcnow(),
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    await create_notification(
        db, user.id, type="job", status="completed",
        title="PDF edit saved",
        message=f"{original_filename} was saved with your edits.",
        link="/history", entity_type="ocr_job", entity_id=job.id,
    )
    logger.info("studio.edit_saved", job_id=job.id[:8])
    return job
