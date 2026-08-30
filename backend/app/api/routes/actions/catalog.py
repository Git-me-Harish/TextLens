"""
Action Catalog Routes

  GET /api/v1/actions/catalog
      — Full action catalog grouped by domain (no auth required)

  GET /api/v1/actions/agent-run/{agent_run_id}/available
      — Actions available for a specific completed agent_run,
        annotated with user's connected credential status
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.action_models import AvailableAction
from app.schemas.action_schemas import AgentRunAvailableActions
from app.services.action_service import get_available_actions

router = APIRouter()


@router.get(
    "/catalog",
    summary="Get the full action catalog grouped by domain",
)
async def get_catalog(db: AsyncSession = Depends(get_db)):
    """
    Returns all available action types grouped by domain.
    Each entry includes label, description, required credentials, and icon.
    No authentication required — used by the frontend action picker.
    """
    result = await db.execute(
        select(AvailableAction)
        .where(AvailableAction.is_enabled == True)
        .order_by(AvailableAction.domain, AvailableAction.sort_order)
    )
    all_actions = result.scalars().all()

    catalog: dict[str, list[dict]] = {}
    for action in all_actions:
        domain = action.domain
        if domain not in catalog:
            catalog[domain] = []
        catalog[domain].append({
            "action_type": action.action_type,
            "label": action.label,
            "description": action.description,
            "requires_credentials": action.requires_credentials or [],
            "icon": action.icon,
            "sort_order": action.sort_order,
        })

    return {"catalog": catalog}


@router.get(
    "/agent-run/{agent_run_id}/available",
    response_model=AgentRunAvailableActions,
    summary="Get available actions for a completed document intelligence run",
)
async def get_available_for_agent_run(
    agent_run_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the actions available for a specific completed agent_run.

    Each action is annotated with:
    - `is_available`: True if the user has all required credentials connected
    - `missing_credentials`: list of service names the user still needs to connect

    This powers the action picker panel in the frontend — it shows which
    actions are ready and which require connecting additional services.
    """
    try:
        available = await get_available_actions(
            db=db,
            agent_run_id=agent_run_id,
            user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return available
