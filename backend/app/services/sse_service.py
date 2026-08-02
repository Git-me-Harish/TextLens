"""
SSE pub/sub service — Redis channel per user.

Two entry points:
  publish_event(user_id, event_type, payload)
    Synchronous — called from Celery tasks (sync context).
    Uses a module-level redis.Redis client (lazy-init, reconnects on failure).

  CHANNEL_FOR(user_id)
    Returns the Redis channel name used by the SSE endpoint subscriber.

Channel naming: sse:user:{user_id}

Event shape (JSON string on the channel):
  {"type": "job_update",   "job_id": "...", "status": "completed", ...}
  {"type": "agent_update", "run_id": "...", "status": "completed", ...}
  {"type": "batch_update", "batch_id": "...", "status": "completed", ...}
"""
import json
import logging

import redis as sync_redis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Module-level sync Redis client — created once per Celery worker process.
_client: sync_redis.Redis | None = None


def _get_client() -> sync_redis.Redis:
    """Return a live sync Redis client, reconnecting if necessary."""
    global _client
    try:
        if _client is None:
            _client = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
        _client.ping()  # fast liveness check
    except Exception:
        _client = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def channel_for(user_id: str) -> str:
    return f"sse:user:{user_id}"


def publish_event(user_id: str, event_type: str, payload: dict) -> None:
    """
    Publish an SSE event to the user's Redis channel.

    Synchronous — safe to call from Celery tasks.
    Errors are logged but never raised so task completion is never blocked.
    """
    try:
        data = json.dumps({"type": event_type, **payload})
        _get_client().publish(channel_for(user_id), data)
        logger.debug("sse.published", user_id=user_id[:8], event_type=event_type)
    except Exception as exc:
        logger.warning("sse.publish_failed", error=str(exc), user_id=user_id[:8])