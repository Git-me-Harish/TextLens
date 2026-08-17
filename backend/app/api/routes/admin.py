"""
Admin panel API — all endpoints require UserRole.admin.

Access is enforced by the `require_admin` dependency from deps.py which
raises HTTP 403 for non-admin authenticated users.

Endpoints

  GET  /admin/stats              — system-wide aggregates
  GET  /admin/users              — all users with per-user job counts
  GET  /admin/users/{id}         — single user detail
  PATCH /admin/users/{id}        — update role / active status
  GET  /admin/jobs               — all jobs (any user) with filters
  GET  /admin/health             — service health check (DB, Redis, MinIO, Voyage)
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.config import settings
from app.db.database import get_db
from app.models.models import (
    AgentRun,
    BatchJob,
    JobStatus,
    OCRJob,
    User,
    UserRole,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


#  Schemas


class UserUpdateRequest(BaseModel):
    role: Optional[str] = None  # "admin" | "user"
    is_active: Optional[bool] = None


#  System stats
@router.get("/stats")
async def system_stats(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    System-wide aggregated statistics.
    Heavy queries — intended for admin dashboards, not end-user APIs.
    """
    since_24h = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    since_7d = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)

    # Users
    user_totals = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((User.is_active.is_(True), 1), else_=0)).label("active"),
                func.sum(case((User.created_at >= since_7d, 1), else_=0)).label(
                    "new_7d"
                ),
            )
        )
    ).one()

    # Jobs
    job_totals = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((OCRJob.created_at >= since_24h, 1), else_=0)).label(
                    "last_24h"
                ),
                func.sum(case((OCRJob.status == JobStatus.failed, 1), else_=0)).label(
                    "failed"
                ),
                func.sum(
                    case((OCRJob.status == JobStatus.processing, 1), else_=0)
                ).label("processing"),
                func.coalesce(func.sum(OCRJob.page_count), 0).label("total_pages"),
            )
        )
    ).one()

    # Agent runs
    agent_total = (
        await db.execute(select(func.count()).select_from(AgentRun))
    ).scalar()

    # Batch jobs
    batch_total = (
        await db.execute(select(func.count()).select_from(BatchJob))
    ).scalar()

    # RAG chunks
    chunk_total = (
        await db.execute(text("SELECT COUNT(*) FROM document_chunks"))
    ).scalar()

    # Failed rate
    job_t = int(job_totals.total or 1)
    job_f = int(job_totals.failed or 0)
    failed_pct = round(job_f / job_t * 100, 1) if job_t else 0

    return {
        "users": {
            "total": int(user_totals.total or 0),
            "active": int(user_totals.active or 0),
            "new_last_7d": int(user_totals.new_7d or 0),
        },
        "jobs": {
            "total": int(job_totals.total or 0),
            "last_24h": int(job_totals.last_24h or 0),
            "currently_processing": int(job_totals.processing or 0),
            "failed_total": job_f,
            "failed_pct": failed_pct,
            "total_pages": int(job_totals.total_pages or 0),
        },
        "agents": {
            "total": int(agent_total or 0),
        },
        "batches": {
            "total": int(batch_total or 0),
        },
        "rag": {
            "total_chunks": int(chunk_total or 0),
        },
    }


#  User management
@router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    search: str = Query(default=""),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Paginated list of all users with per-user job counts.
    Optional `search` filters by email or full_name.
    """
    offset = (page - 1) * per_page

    # Subquery: job count per user
    job_count_sq = (
        select(OCRJob.user_id, func.count().label("job_count"))
        .group_by(OCRJob.user_id)
        .subquery()
    )
    agent_count_sq = (
        select(AgentRun.user_id, func.count().label("agent_count"))
        .group_by(AgentRun.user_id)
        .subquery()
    )

    base = (
        select(
            User,
            func.coalesce(job_count_sq.c.job_count, 0).label("job_count"),
            func.coalesce(agent_count_sq.c.agent_count, 0).label("agent_count"),
        )
        .outerjoin(job_count_sq, job_count_sq.c.user_id == User.id)
        .outerjoin(agent_count_sq, agent_count_sq.c.user_id == User.id)
    )

    if search:
        like = f"%{search.lower()}%"
        base = base.where(
            func.lower(User.email).like(like) | func.lower(User.full_name).like(like)
        )

    total = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                func.lower(User.email).like(f"%{search.lower()}%")
                if search
                else text("1=1")
            )
        )
    ).scalar()

    rows = (
        await db.execute(
            base.order_by(User.created_at.desc()).offset(offset).limit(per_page)
        )
    ).all()

    return {
        "total": int(total or 0),
        "page": page,
        "per_page": per_page,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role.value,
                "is_active": u.is_active,
                "is_verified": u.is_verified,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "job_count": int(jc),
                "agent_count": int(ac),
            }
            for u, jc, ac in rows
        ],
    }


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Single user detail with lifetime stats."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    job_count = (
        await db.execute(select(func.count()).where(OCRJob.user_id == user_id))
    ).scalar()
    agent_count = (
        await db.execute(select(func.count()).where(AgentRun.user_id == user_id))
    ).scalar()

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "stats": {
            "jobs": int(job_count or 0),
            "agents": int(agent_count or 0),
        },
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a user's role or active status.
    Admins cannot demote themselves.
    """
    if user_id == admin.id:
        raise HTTPException(
            400, "Admins cannot modify their own role or status through this endpoint."
        )

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    changed = False

    if body.role is not None:
        if body.role not in ("admin", "user"):
            raise HTTPException(400, "Role must be 'admin' or 'user'")
        user.role = UserRole(body.role)
        changed = True

    if body.is_active is not None:
        user.is_active = body.is_active
        changed = True

    if changed:
        db.add(user)
        await db.commit()
        logger.info(
            "admin.user_updated",
            target_user=user_id[:8],
            by_admin=admin.id[:8],
            role=body.role,
            is_active=body.is_active,
        )

    return {
        "id": user.id,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
    }


#  Job inspection
@router.get("/jobs")
async def list_all_jobs(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    status: str = Query(default=""),
    user_id: str = Query(default="", description="Filter by specific user UUID"),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    List all OCR jobs across all users — admin scope bypass.
    Supports filtering by status and user_id.
    """
    offset = (page - 1) * per_page
    filters = []

    if status:
        try:
            filters.append(OCRJob.status == JobStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status '{status}'")

    if user_id:
        filters.append(OCRJob.user_id == user_id)

    total = (
        await db.execute(
            select(func.count(OCRJob.id)).where(*filters)
            if filters
            else select(func.count(OCRJob.id))
        )
    ).scalar()

    # Join with User to show email
    q = (
        select(OCRJob, User.email)
        .join(User, OCRJob.user_id == User.id)
        .order_by(OCRJob.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    if filters:
        q = q.where(*filters)

    rows = (await db.execute(q)).all()

    return {
        "total": int(total or 0),
        "page": page,
        "per_page": per_page,
        "jobs": [
            {
                "id": j.id,
                "user_email": email,
                "user_id": j.user_id,
                "job_type": j.job_type.value,
                "status": j.status.value,
                "original_filename": j.original_filename,
                "page_count": j.page_count,
                "processing_time_ms": j.processing_time_ms,
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j, email in rows
        ],
    }


#  System health
@router.get("/health")
async def system_health(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Deep system health check — verifies all backend dependencies.
    Returns 200 with degraded components listed; does NOT return 5xx
    so the admin UI can always render the health page.
    """
    health: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {},
    }

    # Database
    try:
        await db.execute(text("SELECT 1"))
        health["components"]["database"] = {"status": "ok"}
    except Exception as exc:
        health["components"]["database"] = {"status": "error", "error": str(exc)}

    # Redis
    try:
        from app.db.redis import get_redis

        r = await get_redis()
        await r.ping()
        health["components"]["redis"] = {"status": "ok"}
    except Exception as exc:
        health["components"]["redis"] = {"status": "error", "error": str(exc)}

    # MinIO
    try:
        from app.services import storage_service

        await storage_service.object_exists("__healthcheck__")
        health["components"]["minio"] = {
            "status": "ok",
            "bucket": settings.MINIO_BUCKET,
        }
    except Exception as exc:
        health["components"]["minio"] = {"status": "error", "error": str(exc)}

    # Voyage AI (just check key is set — don't call API to avoid token usage)
    health["components"]["voyage_ai"] = {
        "status": "ok" if settings.VOYAGE_API_KEY else "not_configured",
        "model": settings.VOYAGE_MODEL,
    }

    # Groq
    health["components"]["groq"] = {
        "status": "ok" if settings.GROQ_API_KEY else "not_configured",
    }

    # pgvector
    try:
        row = await db.execute(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
        ver = row.scalar()
        health["components"]["pgvector"] = {"status": "ok", "version": ver}
    except Exception as exc:
        health["components"]["pgvector"] = {"status": "error", "error": str(exc)}

    all_ok = all(
        c.get("status") in ("ok", "not_configured")
        for c in health["components"].values()
    )
    health["overall"] = "ok" if all_ok else "degraded"
    return health
