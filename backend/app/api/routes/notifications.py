"""
Notification routes — the persisted feed backing the bell dropdown and
dashboard panel. Real-time delivery happens separately over the existing
SSE stream (see sse_service.py / notification_service.py); these routes are
for the history view and read-state management.

  GET  /api/v1/notifications                — list (paginated, newest first)
  POST /api/v1/notifications/{id}/read       — mark one as read
  POST /api/v1/notifications/read-all        — mark every unread one as read
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import Notification, User
from app.schemas.schemas import NotificationListResponse, NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    q = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit)

    total_q = select(func.count(Notification.id)).where(Notification.user_id == user.id)
    unread_q = select(func.count(Notification.id)).where(
        Notification.user_id == user.id, Notification.is_read.is_(False)
    )

    notifications = (await db.execute(q)).scalars().all()
    total = (await db.execute(total_q)).scalar()
    unread_count = (await db.execute(unread_q)).scalar()

    return NotificationListResponse(
        notifications=list(notifications), total=total, unread_count=unread_count
    )


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found.")
    notif.is_read = True
    await db.commit()
    await db.refresh(notif)
    return notif


@router.post("/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await db.commit()
    return {"marked_read": result.rowcount}
