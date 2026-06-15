""" /api/schedules — CRUD for ScheduledBatch jobs """

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, ScheduledBatch

router = APIRouter(prefix="/schedules", tags=["schedules"])

CRON_PRESETS = {
    "hourly":       "0 * * * *",
    "daily_9am":    "0 9 * * *",
    "weekly_mon":   "0 9 * * 1",
    "weekly_fri":   "0 17 * * 5",
    "monthly_1st":  "0 9 1 * *",
}


class ScheduleCreate(BaseModel):
    name: str
    cron_expr: str          # raw cron or preset key
    domain: str
    pipeline_type: str
    drive_folder_id: str | None = None
    user_instructions: str | None = None


class ScheduleOut(BaseModel):
    id: str
    name: str
    cron_expr: str
    domain: str
    pipeline_type: str
    drive_folder_id: str | None
    user_instructions: str | None
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    run_count: int
    created_at: datetime

    class Config:
        from_attributes = True


def _resolve_cron(expr: str) -> str:
    return CRON_PRESETS.get(expr, expr)


def _next_run(cron_expr: str) -> datetime | None:
    try:
        from croniter import croniter
        return croniter(cron_expr, datetime.utcnow()).get_next(datetime)
    except Exception:
        return None


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    res = await db.execute(
        select(ScheduledBatch)
        .where(ScheduledBatch.user_id == user.id)
        .order_by(ScheduledBatch.created_at.desc())
    )
    return res.scalars().all()


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    data: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cron = _resolve_cron(data.cron_expr)
    schedule = ScheduledBatch(
        user_id=user.id,
        name=data.name,
        cron_expr=cron,
        domain=data.domain,
        pipeline_type=data.pipeline_type,
        drive_folder_id=data.drive_folder_id,
        user_instructions=data.user_instructions,
        is_active=True,
        next_run_at=_next_run(cron),
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule


@router.patch("/{schedule_id}/toggle", response_model=ScheduleOut)
async def toggle_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = await db.get(ScheduledBatch, schedule_id)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "Schedule not found")
    s.is_active = not s.is_active
    if s.is_active:
        s.next_run_at = _next_run(s.cron_expr)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = await db.get(ScheduledBatch, schedule_id)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "Schedule not found")
    await db.delete(s)
    await db.commit()


@router.get("/presets")
async def get_presets():
    return [{"key": k, "label": k.replace("_", " ").title(), "cron": v} for k, v in CRON_PRESETS.items()]