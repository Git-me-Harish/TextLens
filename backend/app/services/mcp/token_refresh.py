"""
Google OAuth token refresh.

Why this exists: Google access tokens expire after ~1 hour. Before this,
mcp_google_calendar.py documented the gap explicitly — "no token-refresh
handling… Revisit if expiring tokens turn out to be a real problem in
practice" — and it turned out to be one: the calendar integration silently
started returning 401 about an hour after a user connected, with manual
reconnection as the only recovery. That was tolerable when calendar backed
two actions; it now backs six domains (healthcare, legal, career,
government, education, logistics).

Where the refresh lives, and why here: the MCP proxy route deliberately
never touches the database — that boundary is worth keeping, so refreshing
there would mean handing the route a DB session just for this. The
credential store is already the layer that holds the DB session, decrypts
the blob, and (for lazy key rotation) re-encrypts and persists a mutated
credential back. Refresh is the same shape of operation, so it belongs
next to that, not in the proxy.

Only Google needs this. Every other MCP service in the registry is either
self-hosted with no per-user credential at all (pharmacy, job board,
accounting, email) or has no expiry semantics — so this is deliberately a
Google-specific module rather than a general "token refresher" framework.
"""

from datetime import datetime, timedelta, timezone

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Refresh a little before actual expiry so a token can't die mid-flight
# between this check and the API call that uses it.
_EXPIRY_SKEW_SECONDS = 120


def compute_expires_at(expires_in: int | None) -> str | None:
    """
    Turn Google's `expires_in` (seconds from now) into a stored ISO 8601
    UTC timestamp. Returns None when Google omits it, which is treated
    downstream as "expiry unknown — refresh on next use".
    """
    if not expires_in:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()


def is_expired(credentials: dict) -> bool:
    """
    True when the stored access token is expired, within the skew window, or
    has no recorded expiry at all.

    The no-expiry case covers credentials saved before expires_at was stored.
    Those refresh once on next use and gain an expires_at, so this is a
    one-time cost per legacy credential rather than a refresh on every call.
    """
    expires_at = credentials.get("expires_at")
    if not expires_at:
        return True
    try:
        deadline = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= deadline - timedelta(seconds=_EXPIRY_SKEW_SECONDS)


async def refresh_google_token(credentials: dict) -> dict | None:
    """
    Exchange the stored refresh_token for a fresh access token.

    Returns an updated credentials dict on success, or None on failure —
    callers keep using the existing (stale) credential in that case, so a
    transient Google outage degrades to the pre-existing 401 behaviour
    rather than breaking credential retrieval outright.

    Google does not return a new refresh_token on this grant, so the
    existing one is carried forward.
    """
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        logger.warning("google.refresh_skipped", reason="no refresh_token stored")
        return None

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        logger.error("google.refresh_skipped", reason="GOOGLE_CLIENT_ID/SECRET not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_GOOGLE_TOKEN_URL, data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })
    except Exception as exc:
        logger.error("google.refresh_request_failed", error=str(exc))
        return None

    if resp.status_code != 200:
        # invalid_grant means the user revoked access or the refresh token was
        # rotated out — no amount of retrying fixes that, they must reconnect.
        body = resp.text[:300]
        logger.error("google.refresh_failed", status=resp.status_code, body=body)
        return None

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        logger.error("google.refresh_no_access_token")
        return None

    updated = dict(credentials)
    updated["access_token"] = access_token
    updated["expires_at"] = compute_expires_at(data.get("expires_in"))
    logger.info("google.token_refreshed", expires_at=updated["expires_at"])
    return updated
