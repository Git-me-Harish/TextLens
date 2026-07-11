"""
/api/drive — Google Drive integration.

Import: pick a PDF/image from Drive → download bytes → run as OCRJob
Export: save agent structured result + summary as JSON file to Drive

Uses Google Drive MCP via Anthropic API tool-use calls.
The MCP server (https://drivemcp.googleapis.com/mcp/v1) handles OAuth.
"""
import io
import uuid
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.database import get_db
from app.core.config import settings
from app.models.models import User, OCRJob, AgentRun, JobStatus, JobType
from app.schemas.schemas import JobOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drive", tags=["drive"])

ANTHROPIC_URL  = "https://api.anthropic.com/v1/messages"
DRIVE_MCP_URL  = "https://drivemcp.googleapis.com/mcp/v1"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"


# ── Schemas ──────────────────────────────────────────────────────────

class DriveImportRequest(BaseModel):
    file_id: str          # Google Drive file ID
    job_type: str = "pdf_extract"

class DriveExportRequest(BaseModel):
    run_id: str           # AgentRun to export
    folder_id: str = "root"  # Drive folder to save into


# ── Helpers ───────────────────────────────────────────────────────────

async def _mcp_call(user_message: str, system: str = None) -> dict:
    """Call Claude with Drive MCP server attached. Returns full response."""
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(502, "ANTHROPIC_API_KEY not configured")

    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": user_message}],
        "mcp_servers": [{"type": "url", "url": DRIVE_MCP_URL, "name": "gdrive"}],
    }
    if system:
        body["system"] = system

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            ANTHROPIC_URL,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "mcp-client-2025-04-04",
                "x-api-key": settings.ANTHROPIC_API_KEY,
            },
            json=body,
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"Anthropic API error: {resp.text[:200]}")
    return resp.json()


def _extract_text_from_response(data: dict) -> str:
    """Pull all text blocks from an Anthropic response."""
    return "\n".join(
        block["text"] for block in data.get("content", [])
        if block.get("type") == "text"
    ).strip()


def _extract_tool_result(data: dict) -> str | None:
    """Pull first mcp_tool_result content text."""
    for block in data.get("content", []):
        if block.get("type") == "mcp_tool_result":
            content = block.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text")
            if isinstance(content, str):
                return content
    return None


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/files")
async def list_drive_files(
    query: str = "",
    user: User = Depends(get_current_user),
):
    """List PDF/image files in the user's Drive. Optional search query."""
    mime_filter = "mimeType='application/pdf' or mimeType contains 'image/'"
    search = f"name contains '{query}' and " if query else ""
    prompt = (
        f"List files from Google Drive matching: {search}{mime_filter}. "
        "For each file return: id, name, mimeType, size, modifiedTime. "
        "Return ONLY a JSON array, no markdown."
    )
    try:
        data = await _mcp_call(prompt)
        text = _extract_tool_result(data) or _extract_text_from_response(data)
        # Parse JSON from response
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        files = json.loads(text.strip())
        return {"files": files if isinstance(files, list) else []}
    except json.JSONDecodeError:
        return {"files": [], "raw": text}
    except Exception as exc:
        raise HTTPException(502, f"Drive list failed: {exc}")


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

    # Ask Claude+Drive MCP to get file metadata
    meta_prompt = f"Get metadata for Google Drive file ID: {data.file_id}. Return ONLY JSON with: id, name, mimeType."
    try:
        meta_data = await _mcp_call(meta_prompt)
        meta_text = _extract_tool_result(meta_data) or _extract_text_from_response(meta_data)
        meta_text = meta_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        meta = json.loads(meta_text)
        filename = meta.get("name", f"drive_{data.file_id[:8]}.pdf")
    except Exception:
        filename = f"drive_{data.file_id[:8]}.pdf"

    # Ask Claude+Drive MCP to export/download file content as base64
    dl_prompt = (
        f"Download the content of Google Drive file ID: {data.file_id} "
        "and return it as a base64-encoded string. Return ONLY the base64 string, nothing else."
    )
    dl_data = await _mcp_call(dl_prompt)
    b64_content = (_extract_tool_result(dl_data) or _extract_text_from_response(dl_data) or "").strip()

    if not b64_content:
        raise HTTPException(502, "Drive returned empty file content")

    # Decode and save to upload dir
    import base64
    try:
        file_bytes = base64.b64decode(b64_content)
    except Exception:
        raise HTTPException(502, "Could not decode file from Drive")

    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(upload_dir / f"{uuid.uuid4()}_{filename}")
    with open(file_path, "wb") as f:
        f.write(file_bytes)

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

    job = OCRJob(
        user_id=user.id,
        job_type=job_type_enum,
        status=JobStatus.processing,
        original_filename=filename,
        file_path=file_path,
        file_hash=file_hash,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    job_id = job.id
    await db.commit()

    # Reuse existing processing pipeline
    from app.api.routes.jobs import _process_and_save
    asyncio.ensure_future(_process_and_save(job_id, data.job_type, file_path, {}))

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

    upload_prompt = (
        f"Create a new file in Google Drive folder ID '{data.folder_id}' "
        f"with filename '{drive_filename}' and the following JSON content:\n\n"
        f"{content_json}\n\n"
        "Confirm the file was created and return the file ID."
    )
    try:
        resp_data = await _mcp_call(upload_prompt)
        response_text = _extract_text_from_response(resp_data)
        return {
            "success": True,
            "filename": drive_filename,
            "folder_id": data.folder_id,
            "drive_response": response_text,
        }
    except Exception as exc:
        raise HTTPException(502, f"Drive export failed: {exc}")