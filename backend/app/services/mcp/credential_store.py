"""
Encrypted MCP credential store.

Credentials are encrypted at rest using AES-256-GCM.
The encryption key lives only in settings.MCP_ENCRYPTION_KEY (env var) — never in the DB.

Key rotation:
  Bump settings.MCP_KEY_VERSION when rotating. Rows with old key_version can be
  re-encrypted lazily: on first read, re-encrypt with new key and update the row.

Design rules:
  - Every row gets a unique 16-byte IV (never reuse IV with same key).
  - The GCM tag provides integrity — tampering is detected on decrypt.
  - Decrypted credentials are held in memory only for the duration of a tool call.
  - Never log decrypted credential values.
"""

import json
import os
import secrets
import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.action_models import UserMCPCredential

logger = structlog.get_logger(__name__)


def _get_key(version: int = 1) -> bytes:
    """
    Derive the 32-byte AES key for the given key version.
    In production, key_v1 = settings.MCP_ENCRYPTION_KEY decoded from hex.
    Multiple versions allow rotation without downtime.
    """
    raw = os.environ.get(f"MCP_ENCRYPTION_KEY_V{version}", "")
    if not raw:
        # Fallback to the primary key for version 1 (dev convenience)
        raw = settings.MCP_ENCRYPTION_KEY
    if len(raw) != 64:
        raise ValueError(
            f"MCP_ENCRYPTION_KEY_V{version} must be a 64-char hex string (32 bytes). "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return bytes.fromhex(raw)


def _encrypt(plaintext: dict, key_version: int = 1) -> tuple[bytes, bytes]:
    """
    Encrypt a dict to bytes using AES-256-GCM.
    Returns (ciphertext_with_tag, iv).
    """
    key = _get_key(key_version)
    iv = secrets.token_bytes(16)
    aesgcm = AESGCM(key)
    data = json.dumps(plaintext, separators=(",", ":")).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, data, None)  # no additional data
    return ciphertext, iv


def _decrypt(ciphertext: bytes, iv: bytes, key_version: int = 1) -> dict:
    """
    Decrypt AES-256-GCM ciphertext. Raises on tampering or wrong key.
    """
    key = _get_key(key_version)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

async def save_credential(
    db: AsyncSession,
    user_id: str,
    service_name: str,
    credentials: dict,
) -> UserMCPCredential:
    """
    Encrypt and upsert a credential record for the given user + service.
    """
    key_version = settings.MCP_KEY_VERSION
    ciphertext, iv = _encrypt(credentials, key_version)

    # Check if a row already exists (upsert pattern)
    result = await db.execute(
        select(UserMCPCredential).where(
            UserMCPCredential.user_id == user_id,
            UserMCPCredential.service_name == service_name,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_credentials = ciphertext
        existing.iv = iv
        existing.key_version = key_version
        row = existing
    else:
        row = UserMCPCredential(
            user_id=user_id,
            service_name=service_name,
            encrypted_credentials=ciphertext,
            iv=iv,
            key_version=key_version,
        )
        db.add(row)

    await db.commit()
    await db.refresh(row)
    logger.info(
        "mcp.credential.saved",
        user_id=user_id,
        service_name=service_name,
        key_version=key_version,
    )
    return row


async def get_credential(
    db: AsyncSession,
    user_id: str,
    service_name: str,
) -> dict | None:
    """
    Retrieve and decrypt credentials for a given user + service.
    Returns None if not found.
    Decrypted dict is returned; never persisted after this call.
    """
    result = await db.execute(
        select(UserMCPCredential).where(
            UserMCPCredential.user_id == user_id,
            UserMCPCredential.service_name == service_name,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    try:
        decrypted = _decrypt(row.encrypted_credentials, row.iv, row.key_version)
    except Exception as exc:
        logger.error(
            "mcp.credential.decrypt_failed",
            user_id=user_id,
            service_name=service_name,
            error=str(exc),
        )
        return None

    # Lazy key rotation: if stored with old version, re-encrypt with current key
    current_version = settings.MCP_KEY_VERSION
    if row.key_version != current_version:
        old_version = row.key_version
        try:
            new_ct, new_iv = _encrypt(decrypted, current_version)
            row.encrypted_credentials = new_ct
            row.iv = new_iv
            row.key_version = current_version
            await db.commit()
            logger.info(
                "mcp.credential.key_rotated",
                user_id=user_id,
                service_name=service_name,
                old_version=old_version,
                new_version=current_version,
            )
        except Exception as exc:
            # Rotation failure is non-fatal — credential is still usable
            logger.warning("mcp.credential.rotation_failed", error=str(exc))

    return decrypted


async def delete_credential(
    db: AsyncSession,
    user_id: str,
    service_name: str,
) -> bool:
    """Delete a credential. Returns True if deleted, False if not found."""
    result = await db.execute(
        select(UserMCPCredential).where(
            UserMCPCredential.user_id == user_id,
            UserMCPCredential.service_name == service_name,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return False
    await db.delete(row)
    await db.commit()
    logger.info(
        "mcp.credential.deleted", user_id=user_id, service_name=service_name
    )
    return True


async def list_connected_services(
    db: AsyncSession,
    user_id: str,
) -> list[UserMCPCredential]:
    """Return all credential rows for a user (without decrypting)."""
    result = await db.execute(
        select(UserMCPCredential).where(UserMCPCredential.user_id == user_id)
    )
    return list(result.scalars().all())
