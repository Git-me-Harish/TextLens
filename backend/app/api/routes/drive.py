"""
/api/drive — Google Drive integration.

Import: pick a PDF/image from Drive → download bytes → run as OCRJob
Export: save agent structured result + summary as JSON file to Drive

Uses the Google Drive REST API with a dedicated per-user OAuth credential.
"""
import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.database import get_db
from app.core.config import settings
from app.models.models import User, OCRJob, AgentRun, JobStatus, JobType
from app.schemas.schemas import JobOut
from app.services import storage_service
from app.services.mcp.credential_store import get_credential
from app.worker.tasks import process_ocr_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drive", tags=["drive"])

DRIVE_API_URL = "https://www.googleapis.com/drive/v3"


# ── Schemas ──────────────────────────────────────────────────────────

class DriveImportRequest(BaseModel):
    file_id: str          # Google Drive file ID
    job_type: str = "pdf_extract"

class DriveExportRequest(BaseModel):
    run_id: str           # AgentRun to export
    folder_id: str = "root"  # Drive folder to save into


# ── Helpers ───────────────────────────────────────────────────────────

async def _drive_token(db: AsyncSession, user_id: str) -> str:
    credentials = await get_credential(db, user_id, "google_drive")
    if not credentials or not credentials.get("access_token"):
        raise HTTPException(401, "Connect Google Drive in Settings before using Drive.")
    return credentials["access_token"]


async def _drive_request(
    method: str,
    path: str,
    token: str,
    **kwargs,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            f"{DRIVE_API_URL}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {token}"},
            **kwargs,
        )
    if response.status_code >= 400:
        raise HTTPException(502, f"Google Drive API error: {response.text[:300]}")
    return response


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/files")
async def list_drive_files(
    query: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List PDF/image files in the user's Drive. Optional search query."""
    token = await _drive_token(db, user.id)
    escaped_query = query.replace("'", "\\'")
    name_filter = f" and name contains '{escaped_query}'" if query else ""
    response = await _drive_request(
        "GET",
        "/files",
        token,
        params={
            "q": f"trashed = false{name_filter} and (mimeType = 'application/pdf' or mimeType contains 'image/')",
            "pageSize": 100,
            "orderBy": "modifiedTime desc",
            "spaces": "drive",
            "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink)",
        },
    )
    return {"files": response.json().get("files", [])}


@router.post("/import", response_model=JobOut, status_code=202)
async def import_from_drive(
    data: DriveImportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Download a file from Google Drive and run it as an OCR job.
    Returns a JobOut immediately — poll GET /jobs/{id} for status.
    """
    # Validate job_type
    try:
        job_type_enum = JobType(data.job_type)
    except ValueError:
        raise HTTPException(400, f"Invalid job_type: {data.job_type}")

    token = await _drive_token(db, user.id)
    metadata = (await _drive_request(
        "GET", f"/files/{data.file_id}", token,
        params={"fields": "id,name,mimeType,size"},
    )).json()
    filename = metadata.get("name", f"drive_{data.file_id[:8]}.pdf")
    file_bytes = (await _drive_request(
        "GET", f"/files/{data.file_id}", token,
        params={"alt": "media"},
    )).content

    # Create OCRJob and kick off processing
    import hashlib
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Dedup check
    dup = (await db.execute(
        select(OCRJob).where(
            OCRJob.user_id == user.id,
            OCRJob.job_type == job_type_enum,
            OCRJob.file_hash == file_hash,
            OCRJob.status == JobStatus.completed,
        )
    )).scalar_one_or_none()
    if dup:
        return dup  # return existing job

    object_key = storage_service.build_upload_key(user.id, filename)
    content_type = metadata.get("mimeType", "application/octet-stream")
    await storage_service.upload_bytes(file_bytes, object_key, content_type)

    job = OCRJob(
        user_id=user.id,
        job_type=job_type_enum,
        status=JobStatus.processing,
        original_filename=filename,
        file_path=object_key,
        file_hash=file_hash,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    job_id = job.id
    await db.commit()

    process_ocr_job.delay(job_id)

    return job


@router.post("/export/{run_id}")
async def export_to_drive(
    run_id: str,
    data: DriveExportRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Save an agent run's structured result + summary as a JSON file in Drive.
    """
    res = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id)
    )
    run = res.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Agent run not found")
    if run.status.value != "completed":
        raise HTTPException(400, "Can only export completed runs")

    export_data = {
        "run_id": run.id,
        "pipeline": run.pipeline_type,
        "domain": run.domain,
        "original_filename": run.original_filename,
        "confidence_score": run.confidence_score,
        "processed_at": run.completed_at.isoformat() if run.completed_at else None,
        "summary": run.summary,
        "structured_result": run.structured_result,
    }
    content_json = json.dumps(export_data, indent=2, default=str)
    drive_filename = f"TextLens_{run.pipeline_type}_{run.id[:8]}.json"

    token = await _drive_token(db, user.id)
    try:
        metadata = {"name": drive_filename, "parents": [data.folder_id]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{DRIVE_API_URL}/files",
                params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "metadata": (None, json.dumps(metadata), "application/json"),
                    "file": (drive_filename, content_json, "application/json"),
                },
            )
        if response.status_code >= 400:
            raise HTTPException(502, f"Google Drive API error: {response.text[:300]}")
        response_data = response.json()
        return {
            "success": True,
            "filename": drive_filename,
            "folder_id": data.folder_id,
            "drive_response": response_data,
        }
    except Exception as exc:
        raise HTTPException(502, f"Drive export failed: {exc}")