"""
Webhook delivery service — Track 4: Celery-backed exponential backoff retry.

Delivery lifecycle
──────────────────
  fire_webhook(user_id, event, payload)
    → Finds active webhooks for user + event
    → Attempts first delivery immediately (inline HTTP POST)
    → On failure: dispatches retry_webhook_delivery Celery task (attempt 2)
    → Celery handles attempts 2-5 with exponential backoff

Backoff schedule (delay before each attempt):
  Attempt 1 → 0s       (inline in fire_webhook)
  Attempt 2 → 30s
  Attempt 3 → 5 min
  Attempt 4 → 30 min
  Attempt 5 → 2 hours  (final — marked permanently_failed if this fails)

Each delivery attempt is recorded in webhook_deliveries (append-only).
Webhook.total_deliveries is incremented on every attempt.
"""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.models import Webhook, WebhookDelivery, WebhookEvent

logger = structlog.get_logger(__name__)

TIMEOUT_SECS = 10
MAX_ATTEMPTS = 5
BACKOFF_DELAYS = [0, 30, 300, 1800, 7200]  # seconds before attempt 1..5


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _build_headers(wh: Webhook, event_str: str, payload_bytes: bytes) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-TextLens-Event": event_str,
        "User-Agent": "TextLens-Webhook/2.0",
    }
    if wh.secret:
        headers["X-TextLens-Signature"] = _sign_payload(payload_bytes, wh.secret)
    return headers


async def _http_post(
    url: str, payload_bytes: bytes, headers: dict
) -> tuple[int | None, str | None]:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, content=payload_bytes, headers=headers, timeout=TIMEOUT_SECS
            )
        return resp.status_code, None
    except Exception as exc:
        return None, str(exc)


async def _deliver_one(
    wh: Webhook, event_str: str, payload_bytes: bytes, attempt: int
) -> tuple[bool, int | None, str | None]:
    headers = _build_headers(wh, event_str, payload_bytes)
    status_code, error_msg = await _http_post(wh.target_url, payload_bytes, headers)
    success = status_code is not None and 200 <= status_code < 300
    log = logger.bind(webhook_id=wh.id[:8], event=event_str, attempt=attempt)
    if success:
        log.info("webhook.delivered", status=status_code)
    else:
        log.warning("webhook.attempt_failed", status=status_code, error=error_msg)
    return success, status_code, error_msg


async def _persist_attempt(
    db, wh, event_str, payload_dict, status_code, success, error_msg, attempt
):
    db.add(
        WebhookDelivery(
            webhook_id=wh.id,
            event=event_str,
            payload=payload_dict,
            status_code=status_code,
            success=success,
            error_message=error_msg,
            attempt=attempt,
        )
    )
    wh.last_triggered_at = datetime.utcnow()
    wh.total_deliveries = (wh.total_deliveries or 0) + 1
    db.add(wh)


def _schedule_retry(
    webhook_id: str, event_str: str, payload_json: str, attempt: int
) -> None:
    if attempt > MAX_ATTEMPTS:
        logger.warning("webhook.max_attempts_reached", webhook_id=webhook_id[:8])
        return
    delay = BACKOFF_DELAYS[attempt - 1] if attempt <= len(BACKOFF_DELAYS) else 7200
    try:
        from app.worker.tasks import retry_webhook_delivery

        retry_webhook_delivery.apply_async(
            args=[webhook_id, event_str, payload_json, attempt],
            countdown=delay,
        )
        logger.info(
            "webhook.retry_scheduled",
            webhook_id=webhook_id[:8],
            attempt=attempt,
            delay=delay,
        )
    except Exception as exc:
        logger.error("webhook.schedule_retry_failed", error=str(exc))


async def fire_webhook(
    user_id: str, event: WebhookEvent, payload: dict[str, Any]
) -> None:
    """
    Deliver to all active webhooks for this user + event.
    First attempt is inline. Failures schedule Celery retries.
    Never raises — errors are logged only.
    """
    try:
        payload_bytes = json.dumps(
            {
                "event": event.value,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "data": payload,
            },
            default=str,
        ).encode()
        payload_dict = json.loads(payload_bytes)

        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(Webhook).where(
                    Webhook.user_id == user_id, Webhook.is_active.is_(True)
                )
            )
            webhooks = [
                w for w in res.scalars().all() if event.value in (w.events or [])
            ]
            if not webhooks:
                return

            for wh in webhooks:
                success, status_code, error_msg = await _deliver_one(
                    wh, event.value, payload_bytes, attempt=1
                )
                await _persist_attempt(
                    db,
                    wh,
                    event.value,
                    payload_dict,
                    status_code,
                    success,
                    error_msg,
                    attempt=1,
                )
                if not success:
                    _schedule_retry(
                        wh.id, event.value, payload_bytes.decode(), attempt=2
                    )

            await db.commit()

    except Exception as exc:
        logger.error("webhook.fire_crashed", error=str(exc), exc_info=True)


async def dispatch_event(user_id: str, event: str, payload: dict) -> None:
    """Thin wrapper — accepts event as plain string. Used by batch_service."""
    try:
        event_enum = WebhookEvent(event)
    except ValueError:
        logger.warning("webhook.unknown_event", event=event)
        return
    await fire_webhook(user_id, event_enum, payload)
