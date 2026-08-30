"""
Celery Action Tasks

Two tasks handle the full agent lifecycle:

  execute_action_task(action_run_id, user_id)
    — Runs from PENDING → PLANNING → (AWAITING_APPROVAL | COMPLETED | FAILED)
    — If approval required: pauses, stores plan, publishes SSE event
    — If no approval needed: runs straight through to COMPLETED

  resume_action_task(action_run_id, user_id)
    — Triggered by approval_service after the user approves
    — Picks up the saved plan, resumes agent execution
    — → EXECUTING → COMPLETED | FAILED

SSE events are published to Redis channel:
  "sse:action:{action_run_id}"

The FastAPI SSE endpoint subscribes to this channel and streams events
to the connected browser.

Hard limits enforced via Celery:
  - time_limit=330s (5min30s — agent timeout is 300s, 30s grace)
  - soft_time_limit=310s  — raises SoftTimeLimitExceeded first
"""

import asyncio
import json
from datetime import datetime, timezone

import structlog
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.action_models import ActionRun, AgentTrace
# Must be imported at module load — see action_service.py's import comment for why
# a lazy/local import here is unsafe in the Celery worker process.
from app.models.models import AgentRun
from app.schemas.action_schemas import SSEEventType
from app.services.action_service import (
    get_action_run,
    mark_completed,
    mark_failed,
    mark_planning,
)
from app.services.actions.agent_router import MissingCredentialsError, get_agent
from app.services.approval_service import (
    generate_approval_token,
    store_approval_token,
)
from app.services.notification_service import create_notification, format_action_notification
from app.services.sse_service import publish_event as _publish_global_sse

logger = structlog.get_logger(__name__)


async def _notify_global(db: AsyncSession, run: ActionRun, status: str, error_message: str | None = None) -> None:
    """
    Persist a notification + push to the GLOBAL per-user SSE channel
    (sse:user:{id}), in addition to the existing per-run `_publish_sse`
    below (sse:action:{action_run_id}). The per-run channel only reaches a
    client actively watching this one action (ActionRunner.jsx) — this is
    what makes an action's completion visible to a passive, app-wide
    notification center that was never listening for this specific run.
    """
    title, message = format_action_notification(status, run.action_type, run.domain, error_message)
    notif = await create_notification(
        db, run.user_id, type="action", status=status,
        title=title, message=message, link="/actions/history",
        entity_type="action_run", entity_id=run.id,
    )
    _publish_global_sse(run.user_id, "action_update", {
        "action_run_id": run.id,
        "action_type": run.action_type,
        "domain": run.domain,
        "status": status,
        "notification": notif,
    })


async def _sum_trace_tokens(db: AsyncSession, action_run_id: str) -> int:
    """
    Sum input+output tokens across all AgentTrace rows for an action run.

    Uses an explicit query rather than the ActionRun.traces relationship —
    accessing a lazy relationship attribute on an AsyncSession-loaded object
    outside of an awaited load raises MissingGreenlet, since async SQLAlchemy
    has no implicit lazy-loading.
    """
    result = await db.execute(
        select(
            func.coalesce(func.sum(AgentTrace.input_tokens), 0),
            func.coalesce(func.sum(AgentTrace.output_tokens), 0),
        ).where(AgentTrace.action_run_id == action_run_id)
    )
    input_total, output_total = result.one()
    return int(input_total) + int(output_total)


# ─────────────────────────────────────────────────────────────────────────────
# SSE publisher (Redis pub/sub)
# ─────────────────────────────────────────────────────────────────────────────

def _publish_sse(action_run_id: str, event_type: str, data: dict) -> None:
    """
    Publish an SSE payload to the Redis channel for this action run.
    The FastAPI SSE endpoint subscribes and forwards to the browser.
    Sync (Celery tasks are sync) — uses the sync Redis client.
    """
    import redis  # sync redis for Celery context

    channel = f"sse:action:{action_run_id}"
    payload = json.dumps({"event": event_type, "data": data, "ts": datetime.now(timezone.utc).isoformat()})
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.publish(channel, payload)
        r.close()
    except Exception as exc:
        logger.warning("sse.publish_failed", channel=channel, error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# SSE callback factories (injected into agent.run())
# ─────────────────────────────────────────────────────────────────────────────

def _make_plan_ready_callback(action_run_id: str, token: str, expires_at: datetime):
    async def on_plan_ready(plan):
        _publish_sse(action_run_id, SSEEventType.PLAN_READY, {
            "action_run_id": action_run_id,
            "plan": plan.model_dump(),
            "approval_token": token,
            "approval_expires_at": expires_at.isoformat(),
        })
        _publish_sse(action_run_id, SSEEventType.APPROVAL_REQUIRED, {
            "action_run_id": action_run_id,
            "message": "Please review the plan and approve to continue.",
        })
    return on_plan_ready


def _make_tool_called_callback(action_run_id: str):
    async def on_tool_called(tool_name: str, success: bool, latency_ms: int):
        _publish_sse(action_run_id, SSEEventType.TOOL_CALLED, {
            "action_run_id": action_run_id,
            "tool_name": tool_name,
            "success": success,
            "latency_ms": latency_ms,
        })
    return on_tool_called


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: Execute (PENDING → PLANNING → AWAITING_APPROVAL | COMPLETED)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(
    name="action_tasks.execute_action",
    bind=True,
    max_retries=0,
    time_limit=330,
    soft_time_limit=310,
    acks_late=True,
)
def execute_action_task(self, action_run_id: str, user_id: str) -> dict:
    """
    Main action execution task.
    Runs in a new event loop (Celery workers are sync).
    """
    return asyncio.get_event_loop().run_until_complete(
        _execute_action_async(action_run_id, user_id)
    )


async def _execute_action_async(action_run_id: str, user_id: str) -> dict:
    log = logger.bind(action_run_id=action_run_id, user_id=user_id)
    log.info("action_task.started")

    async with AsyncSessionLocal() as db:
        run: ActionRun | None = None
        try:
            run = await get_action_run(db, action_run_id, user_id)
            await mark_planning(db, run)
            _publish_sse(action_run_id, SSEEventType.EXECUTING, {
                "action_run_id": action_run_id,
                "status": "PLANNING",
                "message": "Building execution plan...",
            })

            # Pull document extraction from the source AgentRun
            result = await db.execute(
                select(AgentRun).where(AgentRun.id == run.agent_run_id)
            )
            source_run = result.scalar_one_or_none()
            document_context = source_run.structured_result or {} if source_run else {}

            # Get the agent (validates credentials exist)
            agent = await get_agent(run.domain, run.action_type, user_id, db)

            # Build HITL callbacks
            token, expires_at = generate_approval_token(action_run_id, user_id)
            on_plan_ready = _make_plan_ready_callback(action_run_id, token, expires_at)
            on_tool_called = _make_tool_called_callback(action_run_id)

            # Run the agent
            agent_result = await agent.run(
                action_run=run,
                document_context=document_context,
                on_plan_ready=on_plan_ready,
                on_tool_called=on_tool_called,
            )

            # If agent returned AWAITING_APPROVAL — persist token and stop
            if isinstance(agent_result, dict) and agent_result.get("status") == "AWAITING_APPROVAL":
                plan_data = agent_result.get("plan", {})
                run.plan = plan_data
                await store_approval_token(db, run, token, expires_at)
                await _notify_global(db, run, "awaiting_approval")
                log.info("action_task.awaiting_approval")
                return {"status": "AWAITING_APPROVAL"}

            # Otherwise agent completed without needing approval
            total_tokens = await _sum_trace_tokens(db, action_run_id)

            await mark_completed(
                db, run,
                result=agent_result,
                total_llm_calls=run.total_llm_calls,
                total_tool_calls=run.total_tool_calls,
                total_tokens=total_tokens,
            )
            _publish_sse(action_run_id, SSEEventType.COMPLETED, {
                "action_run_id": action_run_id,
                "action_result": agent_result,
            })
            await _notify_global(db, run, "completed")
            log.info("action_task.completed")
            return {"status": "COMPLETED"}

        except MissingCredentialsError as exc:
            if run is not None:
                await mark_failed(db, run, str(exc))
                await _notify_global(db, run, "failed", str(exc))
            _publish_sse(action_run_id, SSEEventType.FAILED, {
                "action_run_id": action_run_id,
                "error_message": str(exc),
                "recoverable": True,
                "resolution": "Connect the required services under Settings → Integrations.",
            })
            log.warning("action_task.missing_credentials", error=str(exc))
            return {"status": "FAILED", "error": str(exc)}

        except SoftTimeLimitExceeded:
            msg = "Action timed out after 5 minutes. The agent did not complete in time."
            if run is not None:
                await mark_failed(db, run, msg)
                await _notify_global(db, run, "failed", msg)
            _publish_sse(action_run_id, SSEEventType.FAILED, {
                "action_run_id": action_run_id,
                "error_message": msg,
                "recoverable": False,
            })
            log.error("action_task.timeout")
            return {"status": "FAILED", "error": msg}

        except Exception as exc:
            msg = f"Unexpected error during action execution: {str(exc)[:500]}"
            log.error("action_task.failed", error=str(exc), run_found=run is not None)
            if run is not None:
                try:
                    await mark_failed(db, run, msg)
                    await _notify_global(db, run, "failed", msg)
                    _publish_sse(action_run_id, SSEEventType.FAILED, {
                        "action_run_id": action_run_id,
                        "error_message": msg,
                        "recoverable": False,
                    })
                except Exception as publish_exc:
                    log.error("action_task.failure_handling_failed", error=str(publish_exc))
            return {"status": "FAILED", "error": msg}


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: Resume after approval (EXECUTING → COMPLETED | FAILED)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(
    name="action_tasks.resume_action",
    bind=True,
    max_retries=0,
    time_limit=330,
    soft_time_limit=310,
    acks_late=True,
)
def resume_action_task(self, action_run_id: str, user_id: str) -> dict:
    return asyncio.get_event_loop().run_until_complete(
        _resume_action_async(action_run_id, user_id)
    )


async def _resume_action_async(action_run_id: str, user_id: str) -> dict:
    log = logger.bind(action_run_id=action_run_id, user_id=user_id, resumed=True)
    log.info("resume_task.started")

    async with AsyncSessionLocal() as db:
        run: ActionRun | None = None
        try:
            run = await get_action_run(db, action_run_id, user_id)

            _publish_sse(action_run_id, SSEEventType.EXECUTING, {
                "action_run_id": action_run_id,
                "status": "EXECUTING",
                "message": "Executing approved plan...",
            })

            result = await db.execute(
                select(AgentRun).where(AgentRun.id == run.agent_run_id)
            )
            source_run = result.scalar_one_or_none()
            document_context = source_run.structured_result or {} if source_run else {}

            agent = await get_agent(run.domain, run.action_type, user_id, db)
            on_tool_called = _make_tool_called_callback(action_run_id)

            agent_result = await agent.resume_after_approval(
                action_run=run,
                document_context=document_context,
                saved_plan=run.plan or {},
                on_tool_called=on_tool_called,
            )

            total_tokens = await _sum_trace_tokens(db, action_run_id)

            await mark_completed(
                db, run,
                result=agent_result,
                total_llm_calls=run.total_llm_calls,
                total_tool_calls=run.total_tool_calls,
                total_tokens=total_tokens,
            )
            _publish_sse(action_run_id, SSEEventType.COMPLETED, {
                "action_run_id": action_run_id,
                "action_result": agent_result,
            })
            await _notify_global(db, run, "completed")
            log.info("resume_task.completed")
            return {"status": "COMPLETED"}

        except SoftTimeLimitExceeded:
            msg = "Action execution timed out after approval. No partial changes were made."
            if run is not None:
                await mark_failed(db, run, msg)
                await _notify_global(db, run, "failed", msg)
            _publish_sse(action_run_id, SSEEventType.FAILED, {
                "action_run_id": action_run_id,
                "error_message": msg,
                "recoverable": False,
            })
            return {"status": "FAILED", "error": msg}

        except Exception as exc:
            msg = f"Error during post-approval execution: {str(exc)[:500]}"
            log.error("resume_task.failed", error=str(exc), run_found=run is not None)
            if run is not None:
                try:
                    await mark_failed(db, run, msg)
                    await _notify_global(db, run, "failed", msg)
                    _publish_sse(action_run_id, SSEEventType.FAILED, {
                        "action_run_id": action_run_id,
                        "error_message": msg,
                        "recoverable": False,
                    })
                except Exception as publish_exc:
                    log.error("resume_task.failure_handling_failed", error=str(publish_exc))
            return {"status": "FAILED", "error": msg}
