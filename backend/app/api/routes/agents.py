"""
Agent runs router — Track 1 hardened.

What changed from V1

Dispatch
  background_tasks.add_task(execute_agent_run, ...) → process_agent_run.delay(...)
  The local execute_agent_run helper is removed — that logic now lives in
  app.worker.tasks._run_agent where it has a proper async context and its own
  DB session for the full lifecycle of the run.

Webhook
  asyncio.ensure_future(fire_webhook(db, ...)) was called with a request-scoped
  db session that may already be closed by the time the coroutine ran.
  Now fire_webhook() is called inside the Celery task, which owns its own session.

All GET / DELETE routes are unchanged.
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, OCRJob, AgentRun, AgentStatus
from app.schemas.schemas import AgentRunRequest, AgentRunOut, AgentRunListResponse
from app.services.agent_service import get_pipeline_catalog, classify_document
from app.worker.tasks import process_agent_run

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/agents", tags=["agents"])


# Helpers 
async def _owned_run_or_404(db: AsyncSession, run_id: str, user_id: str) -> AgentRun:
    row = (await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return row


# Routes  
@router.get("/catalog")
async def get_catalog():
    """Return all available domain → pipeline mappings."""
    return get_pipeline_catalog()


@router.post("/classify")
async def classify_job(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Auto-detect domain + best-fit pipeline from an already-extracted OCRJob.
    Requires the job to have result_text (i.e. must be completed).
    """
    job_id = data.get("job_id")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required.")

    res = await db.execute(
        select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id)
    )
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.result_text:
        raise HTTPException(status_code=400, detail="Job has no extracted text to classify.")

    return await classify_document(job.result_text)


@router.post("/run", response_model=AgentRunOut, status_code=202)
async def start_agent_run(
    data: AgentRunRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Start a domain-specific agent pipeline against a completed OCR job.

    Flow:
      1. Validate OCRJob ownership + completion status.
      2. Validate domain + pipeline_type against the catalog.
      3. Create AgentRun record (status = running).
      4. Dispatch process_agent_run Celery task → returns 202 immediately.

    The run result is available via GET /agents/{run_id} once the task completes.
    """
    ocr_res = await db.execute(
        select(OCRJob).where(OCRJob.id == data.job_id, OCRJob.user_id == user.id)
    )
    ocr_job = ocr_res.scalar_one_or_none()
    if not ocr_job:
        raise HTTPException(status_code=404, detail="OCR job not found.")
    if ocr_job.status.value != "completed":
        raise HTTPException(status_code=400, detail="OCR job must be completed before running an agent.")
    if not ocr_job.result_text:
        raise HTTPException(status_code=400, detail="OCR job has no extracted text to analyse.")

    catalog = get_pipeline_catalog()
    if data.domain not in catalog:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {data.domain!r}. Choose from: {list(catalog)}")
    if data.pipeline_type not in catalog[data.domain]["pipelines"]:
        valid = list(catalog[data.domain]["pipelines"])
        raise HTTPException(status_code=400, detail=f"Unknown pipeline {data.pipeline_type!r} for domain {data.domain!r}. Choose from: {valid}")

    run = AgentRun(
        user_id=user.id,
        ocr_job_id=ocr_job.id,
        domain=data.domain,
        pipeline_type=data.pipeline_type,
        status=AgentStatus.running,
        input_text=ocr_job.result_text[:2000],     # preview only; full text sent via task arg
        original_filename=ocr_job.original_filename,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    # Commit before dispatching so the Celery task can safely read this row
    await db.commit()
    await db.refresh(run)

    process_agent_run.delay(
        run.id,
        data.domain,
        data.pipeline_type,
        ocr_job.result_text,            # full text for the agent
        data.user_instructions or "",
    )

    logger.info(
        "agent.queued",
        run_id=run.id[:8],
        domain=data.domain,
        pipeline=data.pipeline_type,
        user_id=user.id[:8],
    )
    return run


@router.get("", response_model=AgentRunListResponse)
async def list_agent_runs(
    page: int = 1,
    per_page: int = 20,
    domain: str = Query(None),
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    per_page = min(per_page, 100)

    base_filter = [AgentRun.user_id == user.id]
    if domain:
        base_filter.append(AgentRun.domain == domain)
    if status:
        base_filter.append(AgentRun.status == status)

    total = (await db.execute(
        select(func.count(AgentRun.id)).where(*base_filter)
    )).scalar()

    runs = (await db.execute(
        select(AgentRun)
        .where(*base_filter)
        .order_by(AgentRun.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )).scalars().all()

    return AgentRunListResponse(runs=list(runs), total=total, page=page, per_page=per_page)


@router.get("/{run_id}", response_model=AgentRunOut)
async def get_agent_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _owned_run_or_404(db, run_id, user.id)


@router.delete("/{run_id}", status_code=204)
async def delete_agent_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = await _owned_run_or_404(db, run_id, user.id)
    await db.delete(run)
    logger.info("agent.deleted", run_id=run_id[:8])