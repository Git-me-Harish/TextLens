"""
Approval Service — Human-in-the-Loop gate management.

Responsibilities:
  1. Generate short-lived signed approval tokens (JWT, 15-min TTL)
     scoped to a single action_run_id.
  2. Verify tokens on approval requests — checks signature, expiry, scope.
  3. Transition action_run status to EXECUTING (approve) or REJECTED (reject).
  4. Enforce ownership — only the action run's owner can approve/reject.

Token design:
  - HS256 JWT signed with settings.SECRET_KEY
  - Claims: sub=action_run_id, uid=user_id, exp=now+15min, iat=now, jti=nonce
  - Single-use enforced by immediately clearing the token on first successful verify
  - Stored (hashed) in action_runs.approval_token for DB-side invalidation
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.action_models import ActionRun

logger = structlog.get_logger(__name__)

_ALGORITHM = "HS256"


# ─────────────────────────────────────────────────────────────────────────────
# Token generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_approval_token(action_run_id: str, user_id: str) -> tuple[str, datetime]:
    """
    Generate a signed JWT approval token.

    Returns:
        (token_string, expires_at)

    The raw token is returned to the client via SSE.
    A SHA-256 hash of the token is stored in the DB — never the raw token.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.APPROVAL_TOKEN_TTL_MINUTES
    )
    payload = {
        "sub": action_run_id,
        "uid": user_id,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
        # Without a nonce the payload is fully determined by the run, the user
        # and the current second — so two tokens issued for the same run inside
        # one second are byte-identical, and re-issuing would NOT invalidate the
        # previous token the way the stored-hash design intends. Reproduced
        # directly when adding the approval-token re-issue endpoint. The jti
        # makes every issue unique, so storing the new hash genuinely retires
        # the old token.
        "jti": secrets.token_urlsafe(16),
        "type": "action_approval",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)
    return token, expires_at


def _hash_token(token: str) -> str:
    """Store a SHA-256 hash of the token, not the raw value."""
    return hashlib.sha256(token.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Approve
# ─────────────────────────────────────────────────────────────────────────────

async def approve_action(
    db: AsyncSession,
    action_run_id: str,
    requesting_user_id: str,
    approval_token: str,
) -> ActionRun:
    """
    Validate approval token and transition action run to EXECUTING.

    Raises:
        ValueError  — ownership mismatch, invalid/expired token, wrong status
    """
    run = await _get_owned_run(db, action_run_id, requesting_user_id)

    if run.status != "AWAITING_APPROVAL":
        raise ValueError(
            f"Action run '{action_run_id}' is in status '{run.status}', "
            "not AWAITING_APPROVAL. Cannot approve."
        )

    # Verify token signature and expiry
    try:
        claims = jwt.decode(
            approval_token,
            settings.SECRET_KEY,
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise ValueError(
            "The approval token has expired (15-minute window). "
            "Please re-initiate the action to get a new approval request."
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Invalid approval token: {exc}")

    # Verify token is scoped to this action_run and this user
    if claims.get("sub") != action_run_id:
        raise ValueError("Approval token is not scoped to this action run.")
    if claims.get("uid") != requesting_user_id:
        raise ValueError("Approval token was not issued for this user.")
    if claims.get("type") != "action_approval":
        raise ValueError("Token type mismatch — not an approval token.")

    # Verify against stored hash (single-use enforcement)
    token_hash = _hash_token(approval_token)
    if run.approval_token != token_hash:
        raise ValueError(
            "Approval token does not match the stored token for this action run. "
            "It may have already been used."
        )

    # Transition to EXECUTING and clear token (single-use)
    run.status = "EXECUTING"
    run.approved_at = datetime.now(timezone.utc)
    run.approval_token = None        # Invalidate — single use
    run.approval_expires_at = None

    await db.commit()
    await db.refresh(run)

    logger.info(
        "approval.granted",
        action_run_id=action_run_id,
        user_id=requesting_user_id,
    )
    return run


# ─────────────────────────────────────────────────────────────────────────────
# Reject
# ─────────────────────────────────────────────────────────────────────────────

async def reject_action(
    db: AsyncSession,
    action_run_id: str,
    requesting_user_id: str,
    reason: str | None = None,
) -> ActionRun:
    """
    Reject a pending action. Transitions to REJECTED.
    No token required — owner identity check is sufficient.
    """
    run = await _get_owned_run(db, action_run_id, requesting_user_id)

    if run.status not in ("AWAITING_APPROVAL", "PENDING", "PLANNING"):
        raise ValueError(
            f"Action run '{action_run_id}' is in status '{run.status}'. "
            "Only PENDING, PLANNING, or AWAITING_APPROVAL runs can be rejected."
        )

    run.status = "REJECTED"
    run.approval_token = None
    run.approval_expires_at = None
    run.error_message = f"Rejected by user. {reason or ''}".strip()
    run.completed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)

    logger.info(
        "approval.rejected",
        action_run_id=action_run_id,
        user_id=requesting_user_id,
        reason=reason,
    )
    return run


# ─────────────────────────────────────────────────────────────────────────────
# Store token hash on action run (called by action_service after planning)
# ─────────────────────────────────────────────────────────────────────────────

async def store_approval_token(
    db: AsyncSession,
    action_run: ActionRun,
    token: str,
    expires_at: datetime,
) -> None:
    """
    Persist a SHA-256 hash of the approval token and update run status.
    Called by action_service immediately after the plan is ready.
    """
    action_run.approval_token = _hash_token(token)
    action_run.approval_expires_at = expires_at
    action_run.status = "AWAITING_APPROVAL"
    await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Cancel (user-initiated from any state)
# ─────────────────────────────────────────────────────────────────────────────

async def cancel_action(
    db: AsyncSession,
    action_run_id: str,
    requesting_user_id: str,
) -> ActionRun:
    """Cancel an action run from any non-terminal state."""
    run = await _get_owned_run(db, action_run_id, requesting_user_id)

    terminal_states = {"COMPLETED", "FAILED", "REJECTED", "CANCELLED"}
    if run.status in terminal_states:
        raise ValueError(
            f"Action run is already in terminal state '{run.status}'. Cannot cancel."
        )

    run.status = "CANCELLED"
    run.approval_token = None
    run.completed_at = datetime.now(timezone.utc)
    run.error_message = "Cancelled by user."

    await db.commit()
    await db.refresh(run)
    return run


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_owned_run(
    db: AsyncSession,
    action_run_id: str,
    user_id: str,
) -> ActionRun:
    """
    Fetch an action run and verify ownership.
    Raises ValueError on not-found or ownership mismatch.
    Never returns another user's run — ownership is verified in SQL.
    """
    result = await db.execute(
        select(ActionRun).where(
            ActionRun.id == action_run_id,
            ActionRun.user_id == user_id,   # ownership enforced at query level
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        # Intentionally vague — don't reveal whether the run exists for another user
        raise ValueError(
            f"Action run '{action_run_id}' not found or access denied."
        )
    return run
