"""
Analytics API — usage statistics for the authenticated user.

All metrics are computed on the fly from existing tables (no denorm table).
This is fast enough for per-user dashboards; switch to a pre-computed
stats table if you add admin-level aggregate queries at scale.

Endpoints

  GET /analytics/summary          — lifetime totals + breakdowns
  GET /analytics/timeline?days=30 — daily job + agent counts for sparklines
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import AgentRun, AgentStatus, BatchJob, JobStatus, OCRJob, User

router = APIRouter(prefix="/analytics", tags=["analytics"])


#  Summary
@router.get("/summary")
async def get_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Lifetime usage summary for the current user.

    Returns:
      jobs     — total, by status, by type, avg processing time, total pages
      agents   — total, by status, by domain, avg confidence
      batches  — total, files processed
      rag      — ingested document chunks (RAG readiness indicator)
    """
    uid = user.id

    #  OCR jobs
    job_totals = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(
                    case((OCRJob.status == JobStatus.completed, 1), else_=0)
                ).label("completed"),
                func.sum(case((OCRJob.status == JobStatus.failed, 1), else_=0)).label(
                    "failed"
                ),
                func.sum(
                    case((OCRJob.status == JobStatus.processing, 1), else_=0)
                ).label("processing"),
                func.round(func.avg(OCRJob.processing_time_ms)).label(
                    "avg_processing_ms"
                ),
                func.coalesce(func.sum(OCRJob.page_count), 0).label("total_pages"),
            ).where(OCRJob.user_id == uid)
        )
    ).one()

    job_by_type_rows = (
        await db.execute(
            select(OCRJob.job_type, func.count().label("n"))
            .where(OCRJob.user_id == uid)
            .group_by(OCRJob.job_type)
        )
    ).all()

    #  Agent runs
    agent_totals = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(
                    case((AgentRun.status == AgentStatus.completed, 1), else_=0)
                ).label("completed"),
                func.sum(
                    case((AgentRun.status == AgentStatus.failed, 1), else_=0)
                ).label("failed"),
                func.sum(
                    case((AgentRun.status == AgentStatus.running, 1), else_=0)
                ).label("running"),
                func.round(func.avg(AgentRun.confidence_score)).label("avg_confidence"),
                func.round(func.avg(AgentRun.processing_time_ms)).label(
                    "avg_processing_ms"
                ),
            ).where(AgentRun.user_id == uid)
        )
    ).one()

    agent_by_domain_rows = (
        await db.execute(
            select(AgentRun.domain, func.count().label("n"))
            .where(AgentRun.user_id == uid)
            .group_by(AgentRun.domain)
        )
    ).all()

    #  Batch jobs
    batch_totals = (
        await db.execute(
            select(
                func.count().label("total"),
                func.coalesce(func.sum(BatchJob.total_files), 0).label("total_files"),
                func.coalesce(func.sum(BatchJob.completed_files), 0).label(
                    "completed_files"
                ),
                func.coalesce(func.sum(BatchJob.failed_files), 0).label("failed_files"),
            ).where(BatchJob.user_id == uid)
        )
    ).one()

    #  RAG ingestion status
    chunk_row = (
        await db.execute(
            text("SELECT COUNT(*) FROM document_chunks WHERE user_id = :uid"),
            {"uid": uid},
        )
    ).scalar()

    #  Assemble response
    return {
        "jobs": {
            "total": int(job_totals.total or 0),
            "completed": int(job_totals.completed or 0),
            "failed": int(job_totals.failed or 0),
            "processing": int(job_totals.processing or 0),
            "avg_processing_ms": int(job_totals.avg_processing_ms or 0),
            "total_pages": int(job_totals.total_pages or 0),
            "by_type": {row.job_type.value: int(row.n) for row in job_by_type_rows},
        },
        "agents": {
            "total": int(agent_totals.total or 0),
            "completed": int(agent_totals.completed or 0),
            "failed": int(agent_totals.failed or 0),
            "running": int(agent_totals.running or 0),
            "avg_confidence": int(agent_totals.avg_confidence or 0),
            "avg_processing_ms": int(agent_totals.avg_processing_ms or 0),
            "by_domain": {row.domain.value: int(row.n) for row in agent_by_domain_rows},
        },
        "batches": {
            "total": int(batch_totals.total or 0),
            "total_files": int(batch_totals.total_files or 0),
            "completed_files": int(batch_totals.completed_files or 0),
            "failed_files": int(batch_totals.failed_files or 0),
        },
        "rag": {
            "total_chunks": int(chunk_row or 0),
            "indexed": int(chunk_row or 0) > 0,
        },
    }


#  Timeline
@router.get("/timeline")
async def get_timeline(
    days: int = Query(default=30, ge=7, le=90, description="Lookback window in days"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Daily activity counts for the past `days` days.
    Returns a list of {date, jobs, agents} dicts suitable for chart sparklines.
    Gaps (days with no activity) are filled with zeros.
    """
    uid = user.id
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # Jobs per day
    job_rows = (
        await db.execute(
            select(
                func.date_trunc("day", OCRJob.created_at).label("day"),
                func.count().label("jobs"),
            )
            .where(OCRJob.user_id == uid, OCRJob.created_at >= since)
            .group_by(func.date_trunc("day", OCRJob.created_at))
            .order_by(func.date_trunc("day", OCRJob.created_at))
        )
    ).all()

    # Agent runs per day
    agent_rows = (
        await db.execute(
            select(
                func.date_trunc("day", AgentRun.created_at).label("day"),
                func.count().label("agents"),
            )
            .where(AgentRun.user_id == uid, AgentRun.created_at >= since)
            .group_by(func.date_trunc("day", AgentRun.created_at))
            .order_by(func.date_trunc("day", AgentRun.created_at))
        )
    ).all()

    # Merge into date-keyed dict
    day_map: dict[str, dict] = {}
    for row in job_rows:
        key = row.day.strftime("%Y-%m-%d")
        day_map.setdefault(key, {"jobs": 0, "agents": 0})["jobs"] = int(row.jobs)
    for row in agent_rows:
        key = row.day.strftime("%Y-%m-%d")
        day_map.setdefault(key, {"jobs": 0, "agents": 0})["agents"] = int(row.agents)

    # Fill missing days with zeros
    result = []
    for i in range(days):
        date = (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")
        entry = day_map.get(date, {"jobs": 0, "agents": 0})
        result.append({"date": date, **entry})

    return {"period_days": days, "data": result}
