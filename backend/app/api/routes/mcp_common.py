"""
Shared plumbing for self-hosted MCP proxy routes (mcp_google_calendar.py, mcp_email.py).

Not a general framework — just the one thing both currently need: verifying
the internal shared-secret header that registry.py's call_mcp_tool() sends
on every outbound call. See INTERNAL_MCP_SHARED_SECRET in config.py for why.
"""

from fastapi import Header, HTTPException, status

from app.core.config import settings


def verify_internal_mcp_secret(x_internal_mcp_secret: str | None = Header(default=None)) -> None:
    """
    FastAPI dependency — raises 401 if the configured shared secret doesn't match.
    A no-op if INTERNAL_MCP_SHARED_SECRET is unset (local dev convenience; set it
    before exposing these routes beyond localhost).
    """
    if not settings.INTERNAL_MCP_SHARED_SECRET:
        return
    if x_internal_mcp_secret != settings.INTERNAL_MCP_SHARED_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid internal MCP secret.",
        )
