import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, OCRJob, AgentRun, AgentStatus
from app.schemas.schemas import AgentRunRequest, AgentRunOut, AgentRunListResponse
from app.services.agent_service import run_agent, get_pipeline_catalog, classify_document

router = APIRouter(prefix="/agents", tags=["agents"])


async def execute_agent_run(run_id: str, domain: str, pipeline_type: str, extracted_text: str, instructions: str):
    from app.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        result = await run_agent(domain, pipeline_type, extracted_text, instructions, db=db)
        run = await db.get(AgentRun, run_id)
        if run:
            run.status = AgentStatus.failed if result.get("error") else AgentStatus.completed
            run.structured_result = result.get("structured_result")
            run.summary = result.get("summary")
            run.confidence_score = result.get("confidence")
            run.error_message = result.get("error")
            run.processing_time_ms = result.get("processing_time_ms")
            run.completed_at = datetime.utcnow()
            await db.commit()

            # Fire webhook
            from app.services.webhook_service import fire_webhook
            from app.models.models import WebhookEvent
            import asyncio
            event = WebhookEvent.agent_completed
            asyncio.ensure_future(fire_webhook(db, run.user_id, event, {
                "run_id": run.id,
                "domain": run.domain,
                "pipeline_type": run.pipeline_type,
                "status": run.status.value,
                "original_filename": run.original_filename,
                "confidence_score": run.confidence_score,
                "processing_time_ms": run.processing_time_ms,
                "error_message": run.error_message,
            }))


@router.get("/catalog")
async def get_catalog():
    return get_pipeline_catalog()


@router.post("/classify")
async def classify_job(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Auto-detect domain + pipeline from an already-extracted OCRJob."""
    job_id = data.get("job_id")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id required")
    res = await db.execute(select(OCRJob).where(OCRJob.id == job_id, OCRJob.user_id == user.id))
    job = res.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.result_text:
        raise HTTPException(status_code=400, detail="Job has no extracted text to classify")
    result = await classify_document(job.result_text)
    return result


@router.post("/run", response_model=AgentRunOut, status_code=202)
async def start_agent_run(
    data: AgentRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(OCRJob).where(OCRJob.id == data.job_id, OCRJob.user_id == user.id))
    ocr_job = result.scalar_one_or_none()
    if not ocr_job:
        raise HTTPException(status_code=404, detail="OCR job not found")
    if ocr_job.status.value != "completed":
        raise HTTPException(status_code=400, detail="OCR job must be completed first")
    if not ocr_job.result_text:
        raise HTTPException(status_code=400, detail="OCR job has no extracted text")

    catalog = get_pipeline_catalog()
    if data.domain not in catalog:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {data.domain}")
    if data.pipeline_type not in catalog[data.domain]["pipelines"]:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {data.pipeline_type}")

    run = AgentRun(
        user_id=user.id,
        ocr_job_id=ocr_job.id,
        domain=data.domain,
        pipeline_type=data.pipeline_type,
        status=AgentStatus.running,
        input_text=ocr_job.result_text[:2000],
        original_filename=ocr_job.original_filename,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    run_id = run.id
    await db.commit()   # must commit before background task reads this row
    await db.refresh(run)
    background_tasks.add_task(execute_agent_run, run_id, data.domain, data.pipeline_type, ocr_job.result_text, data.user_instructions or "")
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
    q = select(AgentRun).where(AgentRun.user_id == user.id)
    cq = select(func.count(AgentRun.id)).where(AgentRun.user_id == user.id)
    if domain:
        q = q.where(AgentRun.domain == domain)
        cq = cq.where(AgentRun.domain == domain)
    if status:
        q = q.where(AgentRun.status == status)
        cq = cq.where(AgentRun.status == status)
    total = (await db.execute(cq)).scalar()
    runs = (await db.execute(q.order_by(AgentRun.created_at.desc()).offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return AgentRunListResponse(runs=list(runs), total=total, page=page, per_page=per_page)


@router.get("/{run_id}", response_model=AgentRunOut)
async def get_agent_run(run_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.delete("/{run_id}", status_code=204)
async def delete_agent_run(run_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    await db.delete(run)