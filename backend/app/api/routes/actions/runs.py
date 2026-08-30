"""
Action Run Routes
  POST /api/v1/actions/run         — start an action
  GET  /api/v1/actions/            — list user's action runs
  GET  /api/v1/actions/{id}        — get single action run
  DELETE /api/v1/actions/{id}      — cancel an action run
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.schemas.action_schemas import (
    ActionRunOut,
    ActionRunStarted,
    StartActionRequest,
)
from app.services.action_service import (
    create_action_run,
    get_action_run,
    list_action_runs,
)
from app.services.approval_service import cancel_action
from app.worker.action_tasks import execute_action_task

router = APIRouter()


@router.post(
    "/run",
    response_model=ActionRunStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an agentic action on a completed document",
)
async def start_action(
    body: StartActionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate an action run against a completed document intelligence result.

    The action executes asynchronously — connect to:
      GET /api/v1/actions/{action_run_id}/stream
    for real-time SSE progress events.

    Approval-required actions will pause and emit an `approval_required` SSE event
    with a token. Use POST /api/v1/actions/{id}/approve to resume.
    """
    try:
        run = await create_action_run(
            db=db,
            user_id=current_user.id,
            agent_run_id=body.agent_run_id,
            action_type=body.action_type,
            user_context=body.user_context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Dispatch to Celery — fire and forget
    execute_action_task.apply_async(
        args=[run.id, current_user.id],
        queue=settings.ACTION_CELERY_QUEUE,
        countdown=0,
    )

    return ActionRunStarted(
        action_run_id=run.id,
        status="PENDING",
        message=(
            f"Action '{body.action_type}' started. "
            f"Connect to /api/v1/actions/{run.id}/stream for real-time progress."
        ),
    )


@router.get(
    "/",
    response_model=list[ActionRunOut],
    summary="List action runs for the current user",
)
async def list_runs(
    domain: str | None = Query(None, description="Filter by domain"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    runs = await list_action_runs(
        db,
        user_id=current_user.id,
        domain=domain,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [ActionRunOut.model_validate(r) for r in runs]


@router.get(
    "/{action_run_id}",
    response_model=ActionRunOut,
    summary="Get a single action run by ID",
)
async def get_run(
    action_run_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        run = await get_action_run(db, action_run_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return ActionRunOut.model_validate(run)


@router.delete(
    "/{action_run_id}",
    summary="Cancel an in-progress action run",
    status_code=status.HTTP_200_OK,
)
async def cancel_run(
    action_run_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        run = await cancel_action(db, action_run_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"action_run_id": run.id, "status": run.status}
