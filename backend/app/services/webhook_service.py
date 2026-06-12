"""
Webhook delivery service.

- Finds all active webhooks for a user matching the event type
- Signs payload with HMAC-SHA256 if secret set
- POSTs to target_url with retry (3 attempts, exponential backoff)
- Logs every attempt to webhook_deliveries
"""
import hashlib
import hmac
import json
import logging
import asyncio
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Webhook, WebhookDelivery, WebhookEvent

logger = logging.getLogger(__name__)

MAX_ATTEMPTS   = 3
TIMEOUT_SECS   = 10
BACKOFF_SECS   = [0, 2, 6]   # delay before each attempt


def _sign(payload_bytes: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(
        secret.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()


async def _attempt(client: httpx.AsyncClient, url: str, payload_bytes: bytes, headers: dict) -> tuple[int | None, str | None]:
    """Single HTTP POST attempt. Returns (status_code, error_message)."""
    try:
        resp = await client.post(url, content=payload_bytes, headers=headers, timeout=TIMEOUT_SECS)
        return resp.status_code, None
    except Exception as exc:
        return None, str(exc)


async def fire_webhook(
    db: AsyncSession,
    user_id: str,
    event: WebhookEvent,
    payload: dict[str, Any],
) -> None:
    """
    Find all active webhooks subscribed to `event` for `user_id` and deliver.
    Called fire-and-forget — errors are logged, never raised.
    """
    try:
        res = await db.execute(
            select(Webhook).where(
                Webhook.user_id == user_id,
                Webhook.is_active == True,
            )
        )
        webhooks = [w for w in res.scalars().all() if event.value in (w.events or [])]

        if not webhooks:
            return

        payload_bytes = json.dumps({
            "event": event.value,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "data": payload,
        }, default=str).encode()

        async with httpx.AsyncClient() as client:
            for wh in webhooks:
                await _deliver(client, db, wh, event, payload_bytes)

    except Exception as exc:
        logger.error(f"[webhook] fire_webhook crashed: {exc}", exc_info=True)


async def _deliver(
    client: httpx.AsyncClient,
    db: AsyncSession,
    wh: Webhook,
    event: WebhookEvent,
    payload_bytes: bytes,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-TextLens-Event": event.value,
        "User-Agent": "TextLens-Webhook/1.0",
    }
    if wh.secret:
        headers["X-TextLens-Signature"] = _sign(payload_bytes, wh.secret)

    status_code = None
    error_msg   = None
    success     = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if BACKOFF_SECS[attempt - 1] > 0:
            await asyncio.sleep(BACKOFF_SECS[attempt - 1])

        status_code, error_msg = await _attempt(client, wh.target_url, payload_bytes, headers)
        success = status_code is not None and 200 <= status_code < 300

        logger.info(f"[webhook:{wh.id[:8]}] attempt={attempt} event={event.value} status={status_code} ok={success}")

        if success:
            break

    # Log delivery attempt
    delivery = WebhookDelivery(
        webhook_id=wh.id,
        event=event.value,
        payload=json.loads(payload_bytes),
        status_code=status_code,
        success=success,
        error_message=error_msg,
        attempt=attempt,
    )
    db.add(delivery)

    # Update webhook metadata
    wh.last_triggered_at = datetime.utcnow()
    wh.total_deliveries  = (wh.total_deliveries or 0) + 1
    db.add(wh)

    try:
        await db.commit()
    except Exception as exc:
        logger.error(f"[webhook:{wh.id[:8]}] DB commit failed: {exc}")


# ── Backward-compat alias used by batch_service ───────────────────────
async def dispatch_event(
    user_id: str,
    event: str,
    payload: dict,
    db: "AsyncSession | None" = None,
) -> None:
    """
    Thin wrapper around fire_webhook that accepts event as a plain string
    and opens its own DB session when one isn't provided.
    """
    from app.db.database import AsyncSessionLocal

    try:
        event_enum = WebhookEvent(event)
    except ValueError:
        logger.warning(f"[webhook] unknown event type: {event!r} — skipping")
        return

    if db is not None:
        await fire_webhook(db, user_id, event_enum, payload)
    else:
        async with AsyncSessionLocal() as session:
            await fire_webhook(session, user_id, event_enum, payload)