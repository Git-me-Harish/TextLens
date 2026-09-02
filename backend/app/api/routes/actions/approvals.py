"""
Action Stream (SSE) + Approval Routes

  GET  /api/v1/actions/{id}/stream          — SSE real-time progress stream
  POST /api/v1/actions/{id}/approval-token  — re-issue the approval token
  POST /api/v1/actions/{id}/approve         — approve a HITL-gated action
  POST /api/v1/actions/{id}/reject          — reject a HITL-gated action
"""

import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.security import decode_token
from app.models.models import User
from app.schemas.action_schemas import (
    ApprovalGranted,
    ActionRejected,
    ApproveActionRequest,
    RejectActionRequest,
    SSEEventType,
)
from app.services.action_service import get_action_run
from app.services.approval_service import (
    approve_action,
    generate_approval_token,
    reject_action,
    store_approval_token,
)
from app.worker.action_tasks import resume_action_task

router = APIRouter()

_SSE_HEARTBEAT_INTERVAL = 15   # seconds between heartbeats
_SSE_MAX_WAIT = 300            # max 5 minutes streaming per connection


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{action_run_id}/stream",
    summary="Stream real-time action progress as Server-Sent Events",
    response_class=StreamingResponse,
)
async def stream_action(
    action_run_id: str,
    request: Request,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Subscribe to SSE events for an action run.

    Events emitted:
      - executing          — status update (planning / executing)
      - plan_ready         — plan produced, approval token included
      - approval_required  — waiting for user to approve
      - tool_called        — a tool was invoked
      - completed          — action finished successfully
      - failed             — action failed
      - heartbeat          — keep-alive every 15 seconds

    The stream closes automatically when a terminal event is received
    or when the client disconnects.
    """
    # EventSource cannot set Authorization headers, so accept the access token
    # as a query parameter for this streaming endpoint.
    payload = decode_token(token or "")
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == payload.get("sub")))
    current_user = result.scalar_one_or_none()
    if not current_user or not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Verify ownership before subscribing
    from app.services.action_service import get_action_run
    try:
        await get_action_run(db, action_run_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Action run not found or access denied.")

    async def event_generator():
        redis_client = aioredis.from_url(settings.REDIS_URL)
        pubsub = redis_client.pubsub()
        channel = f"sse:action:{action_run_id}"
        await pubsub.subscribe(channel)

        terminal_events = {
            SSEEventType.COMPLETED,
            SSEEventType.FAILED,
            SSEEventType.CANCELLED,
        }
        elapsed = 0

        try:
            while elapsed < _SSE_MAX_WAIT:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=_SSE_HEARTBEAT_INTERVAL,
                )

                if message is None:
                    # Heartbeat
                    elapsed += _SSE_HEARTBEAT_INTERVAL
                    yield f"event: {SSEEventType.HEARTBEAT}\ndata: {{}}\n\n"
                    continue

                try:
                    payload = json.loads(message["data"])
                except (json.JSONDecodeError, KeyError):
                    continue

                event_type = payload.get("event", "unknown")
                data_str = json.dumps(payload.get("data", {}))
                yield f"event: {event_type}\ndata: {data_str}\n\n"

                if event_type in terminal_events:
                    break

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await redis_client.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Approve
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{action_run_id}/approval-token",
    summary="Re-issue the approval token for a run awaiting approval",
)
async def reissue_approval_token(
    action_run_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Hand the owner a fresh approval token for a run that is still awaiting
    approval.

    Why this exists: the approval token is delivered exactly once, in the
    `plan_ready` SSE event, and only a SHA-256 hash of it is ever stored — so
    it cannot be read back out of the database. That made a reload (or a
    dropped connection, or opening the run on another device) while a plan sat
    awaiting approval a dead end: the plan was visible via GET /actions/{id},
    but the token needed to act on it was gone, and the action could never be
    approved or completed. Only cancelling was left.

    Re-issuing is no weaker than the original delivery: this endpoint
    authenticates the caller and get_action_run enforces that they own the
    run, which is exactly what the SSE stream checks before streaming the
    token in the first place. The new token replaces the stored hash, so any
    previously issued token stops working — a stale tab cannot approve behind
    the user's back, and the token stays single-use with the same TTL.
    """
    try:
        run = await get_action_run(db, action_run_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if run.status != "AWAITING_APPROVAL":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Action run is '{run.status}', not awaiting approval — "
                "there is nothing to approve."
            ),
        )

    token, expires_at = generate_approval_token(run.id, current_user.id)
    await store_approval_token(db, run, token, expires_at)

    return {
        "action_run_id": run.id,
        "approval_token": token,
        "approval_expires_at": expires_at,
        "plan": run.plan,
    }


@router.post(
    "/{action_run_id}/approve",
    response_model=ApprovalGranted,
    summary="Approve a HITL-gated action plan to begin execution",
)
async def approve_run(
    action_run_id: str,
    body: ApproveActionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit the approval token received in the `plan_ready` SSE event.
    The action transitions to EXECUTING and the resume task is dispatched.

    The token is single-use and expires in 15 minutes.
    """
    try:
        run = await approve_action(
            db=db,
            action_run_id=action_run_id,
            requesting_user_id=current_user.id,
            approval_token=body.approval_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Dispatch resume task to Celery
    resume_action_task.apply_async(
        args=[run.id, current_user.id],
        queue=settings.ACTION_CELERY_QUEUE,
        countdown=0,
    )

    return ApprovalGranted(
        action_run_id=run.id,
        status="EXECUTING",
        message="Action approved. Execution started — follow the SSE stream for progress.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Reject
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{action_run_id}/reject",
    response_model=ActionRejected,
    summary="Reject a HITL-gated action plan",
)
async def reject_run(
    action_run_id: str,
    body: RejectActionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Reject the pending action plan. No external calls will be made.
    The action transitions to REJECTED and can be re-initiated if desired.
    """
    try:
        run = await reject_action(
            db=db,
            action_run_id=action_run_id,
            requesting_user_id=current_user.id,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return ActionRejected(
        action_run_id=run.id,
        status="REJECTED",
        message="Action rejected. No external calls were made.",
    )
