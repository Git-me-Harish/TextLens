"""
Server-Sent Events endpoint.

GET /api/v1/sse/stream?token=<jwt>

Why query-param auth:
  The browser's EventSource API does not support custom headers (no way to
  send Authorization: Bearer ...). The token is validated here exactly the
  same way as the Bearer header approach — short-lived JWT only.

One stream per authenticated user. All event types are multiplexed:
  job_update    — OCR job status changed
  agent_update  — Agent run status changed
  batch_update  — Batch job progress changed
  heartbeat     — Keepalive every HEARTBEAT_INTERVAL seconds

Each SSE endpoint creates its own Redis pub/sub connection — pub/sub
blocks the connection so we cannot share the app's pooled aioredis client.
"""
import asyncio
import json
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Query, HTTPException
from jose import JWTError, jwt
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.services.sse_service import channel_for

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/sse", tags=["sse"])

HEARTBEAT_INTERVAL = 30   # seconds between keepalive pings
POLL_TIMEOUT       = 1.0  # seconds redis.get_message() blocks before returning None


def _validate_token(token: str) -> str:
    """Validate JWT and return user_id (sub claim)."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing sub claim.")
        return user_id
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


@router.get("/stream")
async def sse_stream(token: str = Query(..., description="JWT access token")):
    """
    Open a Server-Sent Events stream for the authenticated user.

    The client should reconnect on disconnect — EventSource does this
    automatically. Each reconnect opens a new Redis subscription.
    """
    user_id = _validate_token(token)
    channel  = channel_for(user_id)
    log      = logger.bind(user_id=user_id[:8])

    async def event_generator():
        # Each SSE connection gets its own Redis connection.
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis_client.pubsub()

        try:
            await pubsub.subscribe(channel)
            log.info("sse.connected", channel=channel)
            last_heartbeat = time.monotonic()

            while True:
                # Heartbeat 
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({
                            "ts": datetime.now(timezone.utc).isoformat()
                        }),
                    }
                    last_heartbeat = now

                # Redis message 
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=POLL_TIMEOUT,
                )
                if msg and msg.get("type") == "message":
                    raw = msg["data"]
                    try:
                        parsed = json.loads(raw)
                        event_type = parsed.get("type", "message")
                        yield {"event": event_type, "data": raw}
                        log.debug("sse.sent", event_type=event_type)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("sse.bad_payload", raw=raw[:200])

                # Yield control so other requests can progress
                await asyncio.sleep(0)

        except asyncio.CancelledError:
            log.info("sse.disconnected")
        except Exception as exc:
            log.error("sse.error", error=str(exc), exc_info=True)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
                await redis_client.aclose()
            except Exception:
                pass

    return EventSourceResponse(event_generator())