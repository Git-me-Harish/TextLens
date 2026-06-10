"""
Webhook delivery service.

On any significant event, call dispatch_event().
It finds all active webhooks for the user subscribed to that event,
fires HTTPS POST with HMAC-SHA256 signed payload, logs the delivery.

Retry policy: up to 3 attempts, exponential backoff (2s, 4s, 8s).
Delivery is fire-and-forget from the caller's perspective —
this runs as a background task and never blocks the main response.
"""
import hashlib
import hmac
import json
import logging
import asyncio
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_TIMEOUT_SECONDS = 10


def _sign_payload(secret: str, payload_bytes: bytes) -> str:
    """HMAC-SHA256 signature — consumers verify via X-TextLens-Signature header."""
    return "sha256=" + hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


async def _deliver(
    webhook_id: str,
    target_url: str,
    secret: str | None,
    event: str,
    payload: dict[str, Any],
    attempt: int = 1,
) -> bool:
    """Attempt a single delivery. Returns True on 2xx, False otherwise."""
    payload_bytes = json.dumps(payload, default=str).encode()
    headers = {
        "Content-Type": "application/json",
        "X-TextLens-Event": event,
        "X-TextLens-Attempt": str(attempt),
        "User-Agent": "TextLens-Webhook/1.0",
    }
    if secret:
        headers["X-TextLens-Signature"] = _sign_payload(secret, payload_bytes)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(target_url, content=payload_bytes, headers=headers)
            return 200 <= response.status_code < 300
    except Exception as exc:
        logger.warning(f"[webhook:{webhook_id[:8]}] delivery failed attempt={attempt}: {exc}")
        return False


async def dispatch_event(
    user_id: str,
    event: str,
    payload: dict[str, Any],
) -> None:
    """
    Find all active user webhooks subscribed to this event,
    deliver to each with retry logic, log every attempt.

    Called after job/agent/batch completion — always in a background task.
    """
    from app.db.database import AsyncSessionLocal
    from app.models.models import Webhook, WebhookDelivery
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Webhook).where(
                Webhook.user_id == user_id,
                Webhook.is_active == True,
            )
        )
        webhooks = result.scalars().all()

    # Filter to only webhooks subscribed to this event
    subscribed = [w for w in webhooks if event in (w.events or [])]
    if not subscribed:
        return

    full_payload = {
        "event": event,
        "timestamp": datetime.utcnow().isoformat(),
        "data": payload,
    }

    for webhook in subscribed:
        success = False
        status_code = None
        error_msg = None

        for attempt in range(1, _MAX_RETRIES + 1):
            ok = await _deliver(
                webhook_id=webhook.id,
                target_url=webhook.target_url,
                secret=webhook.secret,
                event=event,
                payload=full_payload,
                attempt=attempt,
            )
            if ok:
                success = True
                break
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)  # 2s, 4s, 8s

        # Log delivery attempt
        async with AsyncSessionLocal() as db:
            delivery = WebhookDelivery(
                webhook_id=webhook.id,
                event=event,
                payload=full_payload,
                status_code=status_code,
                success=success,
                error_message=error_msg,
                attempt=_MAX_RETRIES if not success else 1,
            )
            db.add(delivery)

            # Update webhook metadata
            wh = await db.get(Webhook, webhook.id)
            if wh:
                wh.last_triggered_at = datetime.utcnow()
                wh.total_deliveries += 1

            await db.commit()

        logger.info(
            f"[webhook:{webhook.id[:8]}] event={event} "
            f"url={webhook.target_url} success={success}"
        )