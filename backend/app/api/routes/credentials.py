"""
Credentials Routes — manage user MCP service integrations

  GET    /api/v1/credentials                              — list connected services
  POST   /api/v1/credentials                               — connect a service (manual API key/token)
  DELETE /api/v1/credentials/{service}                     — disconnect a service
  GET    /api/v1/credentials/google_calendar/connect-url   — start the Google Calendar OAuth flow
  GET    /api/v1/credentials/google_calendar/callback      — Google redirects here after consent
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.schemas.action_schemas import CredentialOut, SaveCredentialRequest
from app.services.mcp.credential_store import (
    delete_credential,
    list_connected_services,
    save_credential,
)
from app.services.mcp.registry import MCP_REGISTRY
from app.services.mcp.token_refresh import compute_expires_at

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/credentials", tags=["MCP Credentials"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_OAUTH_STATE_TYPE = "google_calendar_oauth_state"


def _generate_oauth_state(user_id: str) -> str:
    """
    Short-lived signed token carrying the initiating user's id through the
    Google redirect round-trip — the callback is hit by the browser directly
    (no Authorization header), so this is how it knows whose credentials to save.
    Same pattern as approval_service.py's approval tokens.
    """
    payload = {
        "sub": user_id,
        "type": _OAUTH_STATE_TYPE,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def _verify_oauth_state(state: str) -> str:
    """Returns the user_id encoded in the state token, or raises ValueError."""
    try:
        claims = jwt.decode(state, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise ValueError("This connection link has expired. Please try connecting again.")
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Invalid state token: {exc}")

    if claims.get("type") != _OAUTH_STATE_TYPE:
        raise ValueError("State token type mismatch.")
    user_id = claims.get("sub")
    if not user_id:
        raise ValueError("State token missing user id.")
    return user_id


@router.get(
    "/",
    response_model=list[CredentialOut],
    summary="List all connected MCP services for the current user",
)
async def list_credentials(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns metadata about connected services — never returns credential values.
    Use this to determine which action types are available.
    """
    rows = await list_connected_services(db, current_user.id)
    return [
        CredentialOut(
            service_name=row.service_name,
            connected=True,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get(
    "/services",
    summary="List all supported MCP service names and their required fields",
)
async def list_supported_services():
    """
    Returns the catalog of supported MCP services.
    Use this to understand what credential fields each service expects.
    """
    services = {}
    credential_schemas = {
        "google_calendar": {
            "description": "Google Calendar for appointment and deadline tracking",
            "connection_type": "oauth",
            "fields": {},
        },
        "pharmacy_api": {
            "description": "Medicine search and ordering — runs on this platform's own catalog, nothing to connect.",
            "connection_type": "system",
            "fields": {},
        },
        "job_board_api": {
            "description": "Job search and applications — runs on this platform's own listings, nothing to connect.",
            "connection_type": "system",
            "fields": {},
        },
        "accounting_api": {
            "description": "Expense/invoice tracking — runs on this platform's own ledger, nothing to connect.",
            "connection_type": "system",
            "fields": {},
        },
        "email_api": {
            "description": "Sends through this platform's own email account - nothing to connect.",
            "connection_type": "system",
            "fields": {},
        },
    }
    for service_name, server in MCP_REGISTRY.items():
        entry = credential_schemas.get(service_name, {
            "description": f"{service_name} integration",
            "connection_type": "manual",
            "fields": {"api_key": "Service API key"},
        })
        services[service_name] = {**entry, "mcp_url": server.base_url}
    return {"services": services}


@router.get(
    "/google_calendar/connect-url",
    summary="Get the Google OAuth consent URL to connect Google Calendar",
)
async def google_calendar_connect_url(current_user=Depends(get_current_user)):
    """
    Returns the URL the browser should navigate to in order to grant this app
    Calendar access. The frontend fetches this (authenticated, via the normal
    Bearer header) then does a top-level redirect to the returned URL — the
    user's own JWT never appears in the URL or browser history.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured on this server (GOOGLE_CLIENT_ID is empty).",
        )

    state = _generate_oauth_state(current_user.id)
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
        "response_type": "code",
        "scope": _GOOGLE_CALENDAR_SCOPE,
        "access_type": "offline",   # required to receive a refresh_token
        "prompt": "consent",        # force refresh_token even on repeat connects
        "state": state,
    }
    return {"authorization_url": f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get(
    "/google_calendar/callback",
    summary="Google OAuth callback — exchanges the code and saves credentials",
    include_in_schema=False,  # hit by the browser via redirect, not called directly
)
async def google_calendar_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """
    Not behind get_current_user — Google redirects the browser here with no
    Authorization header. The signed `state` token (see _verify_oauth_state)
    is how this identifies which user to save credentials for.
    """
    integrations_url = f"{settings.FRONTEND_URL}/settings/integrations"

    if error:
        logger.warning("google_calendar.oauth_denied", error=error)
        return RedirectResponse(f"{integrations_url}?error={error}")

    if not code or not state:
        return RedirectResponse(f"{integrations_url}?error=missing_code_or_state")

    try:
        user_id = _verify_oauth_state(state)
    except ValueError as exc:
        logger.warning("google_calendar.oauth_state_invalid", error=str(exc))
        return RedirectResponse(f"{integrations_url}?error=invalid_state")

    async with httpx.AsyncClient(timeout=15) as client:
        token_res = await client.post(_GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
            "grant_type": "authorization_code",
        })

    if token_res.status_code != 200:
        logger.error(
            "google_calendar.token_exchange_failed",
            status=token_res.status_code,
            body=token_res.text[:500],
        )
        return RedirectResponse(f"{integrations_url}?error=token_exchange_failed")

    token_data = token_res.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token:
        return RedirectResponse(f"{integrations_url}?error=no_access_token_returned")
    if not refresh_token:
        # Google omits refresh_token on repeat consents unless prompt=consent forced
        # it — we set that above, so this should be rare, but don't silently drop it.
        logger.warning("google_calendar.no_refresh_token", user_id=user_id)

    # Store when this token dies so credential_store can refresh it before use
    # rather than discovering expiry as a 401 mid-action — see token_refresh.py.
    credentials = {
        "access_token": access_token,
        "expires_at": compute_expires_at(token_data.get("expires_in")),
    }
    if refresh_token:
        credentials["refresh_token"] = refresh_token

    async with AsyncSessionLocal() as db:
        await save_credential(
            db=db,
            user_id=user_id,
            service_name="google_calendar",
            credentials=credentials,
        )

    logger.info("google_calendar.connected", user_id=user_id)
    return RedirectResponse(f"{integrations_url}?connected=google_calendar")


@router.post(
    "/",
    response_model=CredentialOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect an MCP service by saving encrypted credentials",
)
async def save_credentials(
    body: SaveCredentialRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Encrypt and store credentials for an MCP service integration.

    Credentials are encrypted at rest using AES-256-GCM.
    The raw credential values are never stored or logged.

    If credentials already exist for this service, they are replaced.
    """
    row = await save_credential(
        db=db,
        user_id=current_user.id,
        service_name=body.service_name,
        credentials=body.credentials,
    )
    return CredentialOut(
        service_name=row.service_name,
        connected=True,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete(
    "/{service_name}",
    status_code=status.HTTP_200_OK,
    summary="Disconnect an MCP service and delete its credentials",
)
async def delete_credentials(
    service_name: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Permanently delete the stored credentials for the given service.
    Any action runs that require this service will fail until re-connected.
    """
    if service_name not in MCP_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown service '{service_name}'.",
        )

    deleted = await delete_credential(db, current_user.id, service_name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No credentials found for service '{service_name}'.",
        )

    return {
        "service_name": service_name,
        "connected": False,
        "message": f"Credentials for '{service_name}' have been permanently deleted.",
    }
