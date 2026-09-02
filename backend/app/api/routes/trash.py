"""
Trash Routes — recover or permanently remove soft-deleted content.

  GET    /api/v1/trash                       — everything in the user's Trash
  GET    /api/v1/trash/count                 — badge count
  POST   /api/v1/trash/{type}/{id}/restore   — bring one item back
  DELETE /api/v1/trash/{type}/{id}           — delete one item for good
  DELETE /api/v1/trash                       — empty the whole Trash

Types: job | agent_run | action_run | chat_session | batch
(see trash_service.TRASH_TYPES — API keys, webhooks and MCP credentials are
deliberately excluded and keep hard delete.)

Every route is scoped to the authenticated user: an id belonging to someone
else reads as "not found", never as a permission error, so this can't be used
to probe which ids exist.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.services import trash_service
from app.services.trash_service import RETENTION_DAYS, TrashError

router = APIRouter(prefix="/trash", tags=["Trash"])


def _bad_request(exc: TrashError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", summary="List everything in the current user's Trash")
async def list_trash(
    type: str | None = Query(None, description="Filter to one type"),
    limit: int = Query(100, ge=1, le=500),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        items = await trash_service.list_trash(db, current_user.id, type, limit)
    except TrashError as exc:
        raise _bad_request(exc)
    return {"items": items, "retention_days": RETENTION_DAYS}


@router.get("/count", summary="How many items are in Trash")
async def count_trash(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"count": await trash_service.trash_count(db, current_user.id)}


@router.post("/{type}/{item_id}/restore", summary="Restore an item from Trash")
async def restore_item(
    type: str,
    item_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await trash_service.restore(db, type, item_id, current_user.id)
    except TrashError as exc:
        raise _bad_request(exc)
    return {"restored": True, "type": type, "id": item_id}


@router.delete("/{type}/{item_id}", summary="Permanently delete one trashed item")
async def purge_item(
    type: str,
    item_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Irreversible — removes the row and any files it owns in object storage."""
    try:
        await trash_service.purge_one(db, type, item_id, current_user.id)
    except TrashError as exc:
        raise _bad_request(exc)
    return {"deleted": True, "type": type, "id": item_id}


@router.delete("", summary="Empty the Trash permanently")
async def empty_trash(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    removed = await trash_service.empty_trash(db, current_user.id)
    return {"deleted": True, "count": removed}
