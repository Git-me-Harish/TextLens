"""
Corrections & Audit routes.

POST /api/agents/{run_id}/corrections   — submit field corrections on a result
GET  /api/agents/{run_id}/corrections   — get all corrections for a run
GET  /api/audit                         — paginated audit log for the user
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, AgentRun, FieldCorrection, AuditLog
from app.schemas.schemas import (
    FieldCorrectionCreate, FieldCorrectionOut,
    AuditLogOut, AuditLogListResponse,
)

router = APIRouter(tags=["corrections"])


# Corrections
@router.post("/agents/{run_id}/corrections", response_model=list[FieldCorrectionOut], status_code=201)
async def submit_corrections(
    run_id: str,
    data: FieldCorrectionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Submit human corrections to extracted fields.
    Corrections are stored for feedback loop — they don't modify structured_result directly
    (preserving the original AI output for comparison / training).
    """
    run_result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")

    if run.status.value != "completed":
        raise HTTPException(status_code=400, detail="Can only correct completed agent runs")

    if not data.corrections:
        raise HTTPException(status_code=400, detail="No corrections provided")

    created = []
    for correction in data.corrections:
        field_path = correction.get("field_path", "").strip()
        corrected_value = correction.get("corrected_value")

        if not field_path or corrected_value is None:
            continue

        # Get original value by traversing structured_result
        original_value = _get_nested(run.structured_result or {}, field_path)

        fc = FieldCorrection(
            agent_run_id=run.id,
            field_path=field_path,
            original_value=str(original_value) if original_value is not None else None,
            corrected_value=str(corrected_value),
        )
        db.add(fc)
        created.append(fc)

    if not created:
        raise HTTPException(status_code=400, detail="No valid corrections — check field_path and corrected_value")

    # Write correction event to audit log
    audit = AuditLog(
        user_id=user.id,
        action="agent.corrected",
        entity_type="agent_run",
        entity_id=run.id,
        extra_data={"field_count": len(created)},
    )
    db.add(audit)

    await db.commit()
    for fc in created:
        await db.refresh(fc)

    return created


@router.get("/agents/{run_id}/corrections", response_model=list[FieldCorrectionOut])
async def get_corrections(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Verify ownership
    run_result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user.id)
    )
    if not run_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Agent run not found")

    result = await db.execute(
        select(FieldCorrection)
        .where(FieldCorrection.agent_run_id == run_id)
        .order_by(FieldCorrection.created_at.asc())
    )
    return result.scalars().all()


# Audit log 
@router.get("/audit", response_model=AuditLogListResponse)
async def get_audit_log(
    page: int = 1,
    per_page: int = 50,
    action: str = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Paginated audit log — all processing events for the authenticated user."""
    q = select(AuditLog).where(AuditLog.user_id == user.id)
    cq = select(func.count(AuditLog.id)).where(AuditLog.user_id == user.id)

    if action:
        q = q.where(AuditLog.action == action)
        cq = cq.where(AuditLog.action == action)

    total = (await db.execute(cq)).scalar()
    logs = (
        await db.execute(
            q.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()

    return AuditLogListResponse(logs=list(logs), total=total, page=page, per_page=per_page)


# Helper
def _get_nested(obj: dict, path: str):
    """
    Traverse a dot-notation path through a nested dict/list.
    e.g. "line_items[0].amount" → obj["line_items"][0]["amount"]
    Returns None if path not found.
    """
    import re
    parts = re.split(r"\.|\[(\d+)\]", path)
    current = obj
    for part in parts:
        if part is None or part == "":
            continue
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        except (KeyError, IndexError, ValueError):
            return None
    return current
