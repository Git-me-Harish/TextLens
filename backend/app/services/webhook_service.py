"""
Webhook delivery service.
Delivery logic:
  - Finds all active webhooks for the user subscribed to the event
  - Signs payload with HMAC-SHA256 if a secret is configured
  - POSTs to target_url with up to MAX_ATTEMPTS retries + exponential backoff
  - Logs every attempt to webhook_deliveries (append-only)
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.models import Webhook, WebhookDelivery, WebhookEvent

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 3
TIMEOUT_SECS = 10
BACKOFF_SECS = [0, 2, 6]  # delay before attempt 1, 2, 3


# HMAC signing 
def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# Single HTTP attempt 
async def _attempt(
    client: httpx.AsyncClient,
    url: str,
    payload_bytes: bytes,
    headers: dict,
) -> tuple[int | None, str | None]:
    """Single POST attempt. Returns (status_code, error_message)."""
    try:
        resp = await client.post(url, content=payload_bytes, headers=headers, timeout=TIMEOUT_SECS)
        return resp.status_code, None
    except Exception as exc:
        return None, str(exc)


# Per-webhook delivery with retry 
async def _deliver(
    client: httpx.AsyncClient,
    wh: Webhook,
    event: WebhookEvent,
    payload_bytes: bytes,
) -> tuple[bool, int | None, str | None, int]:
    """
    Attempt delivery of `payload_bytes` to `wh.target_url`.
    Returns (success, status_code, error_msg, final_attempt_number).
    Does NOT touch the DB — caller persists the result.
    """
    headers = {
        "Content-Type": "application/json",
        "X-TextLens-Event": event.value,
        "User-Agent": "TextLens-Webhook/2.0",
    }
    if wh.secret:
        headers["X-TextLens-Signature"] = _sign_payload(payload_bytes, wh.secret)

    status_code: int | None = None
    error_msg: str | None = None
    success = False
    final_attempt = 1

    for attempt in range(1, MAX_ATTEMPTS + 1):
        import asyncio
        if BACKOFF_SECS[attempt - 1] > 0:
            await asyncio.sleep(BACKOFF_SECS[attempt - 1])

        status_code, error_msg = await _attempt(client, wh.target_url, payload_bytes, headers)
        success = status_code is not None and 200 <= status_code < 300
        final_attempt = attempt

        log = logger.bind(webhook_id=wh.id[:8], event=event.value, attempt=attempt, status=status_code)
        if success:
            log.info("webhook.delivered")
            break
        log.warning("webhook.attempt_failed", error=error_msg)

    return success, status_code, error_msg, final_attempt


# Public interface
async def fire_webhook(
    user_id: str,
    event: WebhookEvent,
    payload: dict[str, Any],
) -> None:
    """Find all active webhooks for `user_id` subscribed to `event` and deliver."""
    try:
        payload_bytes = json.dumps(
            {
                "event": event.value,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "data": payload,
            },
            default=str,
        ).encode()

        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(Webhook).where(
                    Webhook.user_id == user_id,
                    Webhook.is_active.is_(True),
                )
            )
            webhooks = [
                w for w in res.scalars().all()
                if event.value in (w.events or [])
            ]

            if not webhooks:
                return

            async with httpx.AsyncClient() as client:
                for wh in webhooks:
                    success, status_code, error_msg, attempt = await _deliver(
                        client, wh, event, payload_bytes
                    )

                    # Persist delivery record
                    db.add(WebhookDelivery(
                        webhook_id=wh.id,
                        event=event.value,
                        payload=json.loads(payload_bytes),
                        status_code=status_code,
                        success=success,
                        error_message=error_msg,
                        attempt=attempt,
                    ))

                    # Update webhook metadata
                    wh.last_triggered_at = datetime.utcnow()
                    wh.total_deliveries = (wh.total_deliveries or 0) + 1
                    db.add(wh)

            await db.commit()

    except Exception as exc:
        logger.error("webhook.fire_crashed", error=str(exc), exc_info=True)


async def dispatch_event(
    user_id: str,
    event: str,
    payload: dict,
) -> None:
    """
    Backward-compat thin wrapper — accepts event as a plain string.
    Used by batch_service and scheduled tasks.
    """
    try:
        event_enum = WebhookEvent(event)
    except ValueError:
        logger.warning("webhook.unknown_event", event=event)
        return

    await fire_webhook(user_id, event_enum, payload)