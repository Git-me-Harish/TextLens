"""
API Key management routes — enterprise access layer.

POST   /api/keys           — create new key (returns plain key once, then never again)
GET    /api/keys           — list all user keys (prefixes only, never hashes)
DELETE /api/keys/{id}      — revoke key
PATCH  /api/keys/{id}      — update name or monthly_limit

POST   /api/webhooks       — register webhook endpoint
GET    /api/webhooks        — list webhooks
DELETE /api/webhooks/{id}  — delete webhook
GET    /api/webhooks/{id}/deliveries — delivery history for debugging

Key design:
  - Plain key generated once: tl_live_{uuid4_no_dashes}
  - Stored as bcrypt hash — never retrievable after creation
  - key_prefix (first 12 chars) shown in UI for identification
  - API auth via X-API-Key header (separate from Bearer JWT)
"""
import hashlib
import secrets
import logging
from datetime import datetime

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User, APIKey, Webhook, WebhookDelivery, WebhookEvent
from app.schemas.schemas import (
    APIKeyCreate, APIKeyOut, APIKeyListResponse,
    WebhookCreate, WebhookOut, WebhookDeliveryOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["api-keys"])

_KEY_PREFIX = "tl_live_"


def _generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.
    Returns: (plain_key, key_prefix, key_hash)
    """
    random_part = secrets.token_hex(24)  # 48 hex chars
    plain_key = f"{_KEY_PREFIX}{random_part}"
    key_prefix = plain_key[:12]
    key_hash = bcrypt.hashpw(plain_key.encode(), bcrypt.gensalt(rounds=12)).decode()
    return plain_key, key_prefix, key_hash


def verify_api_key(plain_key: str, key_hash: str) -> bool:
    """Verify a plain API key against its bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_key.encode(), key_hash.encode())
    except Exception:
        return False


# ──────────────────────────────── API Keys ─────────────────────────────

@router.post("/keys", response_model=APIKeyOut, status_code=201)
async def create_api_key(
    data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Create a new API key. The plain key is returned ONCE in this response.
    It cannot be retrieved again — store it securely on your end.
    """
    # Limit: 10 active keys per user
    existing_count = (
        await db.execute(
            select(func.count(APIKey.id)).where(
                APIKey.user_id == user.id,
                APIKey.is_active == True,
            )
        )
    ).scalar()

    if existing_count >= 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 active API keys per user. Revoke an existing key first."
        )

    plain_key, key_prefix, key_hash = _generate_api_key()

    api_key = APIKey(
        user_id=user.id,
        name=data.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        is_active=True,
        monthly_limit=data.monthly_limit,
        expires_at=data.expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    logger.info(f"[api_key] user={user.id[:8]} created key={key_prefix}…")

    # Attach plain key to response — only time it's returned
    result = APIKeyOut.model_validate(api_key)
    result.plain_key = plain_key
    return result


@router.get("/keys", response_model=APIKeyListResponse)
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(APIKey)
        .where(APIKey.user_id == user.id)
        .order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()
    return APIKeyListResponse(keys=list(keys), total=len(keys))


@router.patch("/keys/{key_id}", response_model=APIKeyOut)
async def update_api_key(
    key_id: str,
    data: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    if data.name:
        key.name = data.name
    if data.monthly_limit is not None:
        key.monthly_limit = data.monthly_limit
    if data.expires_at is not None:
        key.expires_at = data.expires_at

    await db.commit()
    await db.refresh(key)
    return key


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Soft delete — mark inactive, preserve for audit log
    key.is_active = False
    await db.commit()
    logger.info(f"[api_key] user={user.id[:8]} revoked key={key.key_prefix}…")


# ──────────────────────────────── Webhooks ─────────────────────────────

VALID_EVENTS = {e.value for e in WebhookEvent}


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(
    data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Validate events
    invalid = [e for e in data.events if e not in VALID_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid events: {invalid}. Valid: {sorted(VALID_EVENTS)}"
        )

    if not data.events:
        raise HTTPException(status_code=400, detail="At least one event required")

    # Limit: 20 webhooks per user
    count = (
        await db.execute(
            select(func.count(Webhook.id)).where(Webhook.user_id == user.id)
        )
    ).scalar()
    if count >= 20:
        raise HTTPException(status_code=400, detail="Maximum 20 webhooks per user")

    webhook = Webhook(
        user_id=user.id,
        name=data.name,
        target_url=data.target_url,
        events=data.events,
        secret=data.secret or None,
        is_active=True,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Webhook)
        .where(Webhook.user_id == user.id)
        .order_by(Webhook.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.user_id == user.id)
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(wh)
    await db.commit()


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def get_webhook_deliveries(
    webhook_id: str,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delivery history — useful for debugging failed deliveries."""
    # Verify ownership
    wh_result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.user_id == user.id)
    )
    if not wh_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Webhook not found")

    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.patch("/webhooks/{webhook_id}/toggle", response_model=WebhookOut)
async def toggle_webhook(
    webhook_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enable / disable a webhook without deleting it."""
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.user_id == user.id)
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")

    wh.is_active = not wh.is_active
    await db.commit()
    await db.refresh(wh)
    return wh