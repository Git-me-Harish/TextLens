"""
Action Service — orchestrates the full action run lifecycle.

Responsibilities:
  1. Validate the agent_run_id belongs to the user and is in a completed state.
  2. Pull the structured document extraction from the AgentRun record.
  3. Create an ActionRun DB record (status=PENDING).
  4. Check available actions for the detected domain.
  5. Dispatch to the Celery action task.
  6. Provide listing, detail fetch, and available action resolution.

The actual agent execution lives in worker/action_tasks.py (Celery).
This service is the synchronous orchestrator — it never calls the LLM directly.
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_models import ActionRun, AvailableAction, UserMCPCredential
# Must be imported at module load, not lazily inside a function: ActionRun.agent_run_id
# carries a string ForeignKey("agent_runs.id"), which SQLAlchemy only resolves once
# AgentRun's table has actually been registered on Base.metadata. In a process that
# never imports app.main (e.g. the Celery worker — see worker/action_tasks.py), a
# lazy import deferred until after the first ActionRun flush is too late and raises
# NoReferencedTableError.
from app.models.models import AgentRun
from app.schemas.action_schemas import (
    ActionRunOut,
    AvailableActionOut,
    AgentRunAvailableActions,
)
from app.services.mcp.registry import get_required_services

logger = structlog.get_logger(__name__)

# Create action run
async def create_action_run(
    db: AsyncSession,
    user_id: str,
    agent_run_id: str,
    action_type: str,
    user_context: str | None,
) -> ActionRun:
    """
    Validate the source agent_run, resolve domain, create ActionRun record.

    Raises:
        ValueError  — agent_run not found/owned, not completed, action not
                      available for domain, or agent_run has no structured result.
    """
    # Verify the source agent_run belongs to this user and is complete
    agent_run = await _get_completed_agent_run(db, agent_run_id, user_id)

    domain = _agent_run_domain(agent_run)
    if not domain:
        raise ValueError(
            "The source document has no detected domain. "
            "Run the document through the intelligence pipeline first."
        )

    # Validate action_type is allowed for this domain
    action_catalog = await _get_catalog_entry(db, domain, action_type)
    if not action_catalog:
        raise ValueError(
            f"Action '{action_type}' is not available for domain '{domain}'. "
            f"Use GET /api/v1/actions/agent-run/{agent_run_id}/available to see valid actions."
        )
    if not action_catalog.is_enabled:
        raise ValueError(f"Action '{action_type}' is currently disabled.")

    run = ActionRun(
        user_id=user_id,
        agent_run_id=agent_run_id,
        action_type=action_type,
        domain=domain,
        status="PENDING",
        user_context=user_context,
        approval_required=bool(get_required_services(action_type)),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    logger.info(
        "action_service.run_created",
        action_run_id=run.id,
        user_id=user_id,
        domain=domain,
        action_type=action_type,
    )
    return run

# Fetch
async def get_action_run(
    db: AsyncSession,
    action_run_id: str,
    user_id: str,
) -> ActionRun:
    """Fetch an action run, enforcing ownership."""
    result = await db.execute(
        select(ActionRun).where(
            ActionRun.id == action_run_id,
            ActionRun.user_id == user_id,
            ActionRun.deleted_at.is_(None),   # trashed reads as gone
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise ValueError(f"Action run '{action_run_id}' not found or access denied.")
    return run


async def list_action_runs(
    db: AsyncSession,
    user_id: str,
    domain: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[ActionRun]:
    """List action runs for a user with optional filters."""
    query = select(ActionRun).where(
        ActionRun.user_id == user_id, ActionRun.deleted_at.is_(None)
    )
    if domain:
        query = query.where(ActionRun.domain == domain)
    if status:
        query = query.where(ActionRun.status == status)
    query = query.order_by(ActionRun.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


# Available actions for a given agent_run
async def get_available_actions(
    db: AsyncSession,
    agent_run_id: str,
    user_id: str,
) -> AgentRunAvailableActions:
    """
    Return the list of available actions for a completed agent_run,
    annotated with which credential services the user is missing.
    """
    agent_run = await _get_completed_agent_run(db, agent_run_id, user_id)
    domain = _agent_run_domain(agent_run)

    # Fetch catalog for domain
    result = await db.execute(
        select(AvailableAction).where(
            AvailableAction.domain == domain,
            AvailableAction.is_enabled == True,
        ).order_by(AvailableAction.sort_order)
    )
    catalog = list(result.scalars().all())

    # Fetch which services this user has connected
    cred_result = await db.execute(
        select(UserMCPCredential.service_name).where(
            UserMCPCredential.user_id == user_id
        )
    )
    connected_services = set(cred_result.scalars().all())

    actions_out: list[AvailableActionOut] = []
    for entry in catalog:
        required = entry.requires_credentials or []
        missing = [s for s in required if s not in connected_services]
        actions_out.append(
            AvailableActionOut(
                action_type=entry.action_type,
                label=entry.label,
                description=entry.description,
                requires_credentials=required,
                icon=entry.icon,
                is_available=len(missing) == 0,
                missing_credentials=missing,
            )
        )

    return AgentRunAvailableActions(
        agent_run_id=agent_run_id,
        domain=domain,
        available_actions=actions_out,
    )


# Status transitions (called by Celery worker)
async def mark_planning(db: AsyncSession, action_run: ActionRun) -> None:
    action_run.status = "PLANNING"
    await db.commit()


async def mark_failed(
    db: AsyncSession,
    action_run: ActionRun,
    error_message: str,
) -> None:
    action_run.status = "FAILED"
    action_run.error_message = error_message[:2000]
    action_run.completed_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_completed(
    db: AsyncSession,
    action_run: ActionRun,
    result: dict,
    total_llm_calls: int,
    total_tool_calls: int,
    total_tokens: int,
) -> None:
    action_run.status = "COMPLETED"
    action_run.action_result = result
    action_run.total_llm_calls = total_llm_calls
    action_run.total_tool_calls = total_tool_calls
    action_run.total_tokens_used = total_tokens
    action_run.completed_at = datetime.now(timezone.utc)
    await db.commit()


async def _get_completed_agent_run(db: AsyncSession, agent_run_id: str, user_id: str):
    """
    Fetch an AgentRun that is COMPLETED and owned by user_id.
    Raises ValueError if not found, not owned, or not completed.
    """
    result = await db.execute(
        select(AgentRun).where(
            AgentRun.id == agent_run_id,
            AgentRun.user_id == user_id,
        )
    )
    agent_run = result.scalar_one_or_none()
    if not agent_run:
        raise ValueError(
            f"Agent run '{agent_run_id}' not found or access denied."
        )
    if agent_run.status != "completed":
        raise ValueError(
            f"Agent run '{agent_run_id}' is in status '{agent_run.status}'. "
            "Only completed document intelligence runs can trigger actions."
        )
    return agent_run


def _agent_run_domain(agent_run) -> str | None:
    """Return the existing TextLens AgentRun domain as a plain string."""
    domain = getattr(agent_run, "domain", None)
    if domain is None:
        return None
    return getattr(domain, "value", domain)


async def _get_catalog_entry(
    db: AsyncSession,
    domain: str,
    action_type: str,
) -> AvailableAction | None:
    result = await db.execute(
        select(AvailableAction).where(
            AvailableAction.domain == domain,
            AvailableAction.action_type == action_type,
        )
    )
    return result.scalar_one_or_none()
