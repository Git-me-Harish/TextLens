"""
Notification service — creates persisted Notification rows and formats
their display text, called alongside the existing SSE push at every place
background work finishes (OCR job, agent pipeline run, agentic action).

Design: the backend formats title/message ONCE here; both the persisted row
and the live SSE payload carry that same text, so the frontend never
duplicates this formatting logic and the two can't drift apart.
"""
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Notification

logger = structlog.get_logger(__name__)


async def create_notification(
    db: AsyncSession,
    user_id: str,
    type: str,
    status: str,
    title: str,
    message: str | None,
    link: str | None,
    entity_type: str | None,
    entity_id: str | None,
) -> dict:
    """
    Persist a notification and return it as a plain dict (JSON-safe for
    embedding directly in an SSE payload — Celery tasks are sync-wrapped-async
    and the caller publishes to Redis right after this).
    """
    notif = Notification(
        user_id=user_id,
        type=type,
        status=status,
        title=title,
        message=message,
        link=link,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    logger.info("notification.created", user_id=user_id, type=type, status=status)
    return {
        "id": notif.id,
        "type": notif.type,
        "status": notif.status,
        "title": notif.title,
        "message": notif.message,
        "link": notif.link,
        "entity_type": notif.entity_type,
        "entity_id": notif.entity_id,
        "is_read": notif.is_read,
        "created_at": notif.created_at.isoformat(),
    }


# ── Title/message formatting per event source ──────────────────────────────

def format_job_notification(
    status: str, job_type: str, filename: str, error_message: str | None
) -> tuple[str, str | None]:
    pretty_type = job_type.replace("_", " ").title()
    if status == "completed":
        return f"{pretty_type} complete", f"{filename} finished processing."
    return f"{pretty_type} failed", error_message or f"{filename} could not be processed."


def format_agent_notification(
    status: str, domain: str, pipeline_type: str, filename: str, error_message: str | None
) -> tuple[str, str | None]:
    pretty_pipeline = pipeline_type.replace("_", " ").title()
    if status == "completed":
        return f"{pretty_pipeline} complete", f"{filename} — {domain} pipeline finished."
    return f"{pretty_pipeline} failed", error_message or f"{filename} — {domain} pipeline failed."


def format_action_notification(
    status: str, action_type: str, domain: str, error_message: str | None
) -> tuple[str, str | None]:
    pretty_action = action_type.replace("_", " ").title()
    if status == "completed":
        return f"{pretty_action} complete", f"Your {domain} action finished successfully."
    if status == "awaiting_approval":
        return f"{pretty_action} needs approval", "Review the plan before it proceeds — this expires in 15 minutes."
    return f"{pretty_action} failed", error_message or "The action could not be completed."
