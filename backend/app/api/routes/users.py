from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user, require_admin
from app.db.database import get_db
from app.models.models import User, OCRJob, AgentRun
from app.schemas.schemas import UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
async def update_me(data: UserUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if data.full_name:
        user.full_name = data.full_name
    await db.flush()
    return user


@router.get("/me/stats")
async def get_my_stats(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    total = (await db.execute(select(func.count(OCRJob.id)).where(OCRJob.user_id == user.id, OCRJob.deleted_at.is_(None)))).scalar()
    completed = (await db.execute(select(func.count(OCRJob.id)).where(OCRJob.user_id == user.id, OCRJob.status == "completed", OCRJob.deleted_at.is_(None)))).scalar()
    agent_runs = (await db.execute(select(func.count(AgentRun.id)).where(AgentRun.user_id == user.id, AgentRun.deleted_at.is_(None)))).scalar()
    return {"total_jobs": total, "completed_jobs": completed, "agent_runs": agent_runs}


@router.get("", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()
