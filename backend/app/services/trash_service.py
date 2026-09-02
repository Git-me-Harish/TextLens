"""
Trash — soft delete, restore, and purge.

Deleting from Extraction History or Pipeline History used to be immediate and
irreversible, and for jobs it also removed the source and result objects from
MinIO — so a misclick permanently destroyed a document and everything derived
from it, with nothing left to recover even in principle.

The model is deliberately boring: `deleted_at IS NULL` means live, anything
else means trashed. Nothing is physically removed until the purge, and the
purge is the only place that touches object storage.

Scope — content and history only:
    ocr_jobs, agent_runs, action_runs, chat_sessions, batch_jobs

API keys, webhooks and connected MCP credentials keep hard delete on purpose.
Those are secrets and configuration: "delete" there has to mean gone now, and
a revoked key recoverable from a trash can for 30 days is a security problem
rather than a convenience.

One rule worth stating because it is easy to get wrong: restoring must give
back something that still works. That is why file cleanup is deferred to
purge — a restore that returns a row pointing at an object already deleted
from MinIO is not a restore, it is a broken record with a friendly name.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_models import ActionRun
from app.models.models import AgentRun, BatchJob, ChatSession, OCRJob
from app.services import storage_service

logger = structlog.get_logger(__name__)

# How long a trashed item is recoverable before the nightly purge removes it
# for good, along with its objects in MinIO.
RETENTION_DAYS = 30


@dataclass(frozen=True)
class TrashType:
    """One trashable resource: how to find it, name it, and clean it up."""
    key: str                      # stable id used in the API surface
    model: Any
    label: str                    # human-readable, singular
    title_of: Callable[[Any], str]
    # Object-storage keys owned by this row, removed only at purge time.
    object_keys: Callable[[Any], list[str]] = lambda _row: []


def _v(value: Any) -> str:
    """
    Render a column value for display, unwrapping Enum members.

    AgentRun.domain is a SQLAlchemy Enum, so plain f-string interpolation
    produces "AgentDomain.finance" rather than "finance" — which is exactly
    what the Trash list showed as an item title before this. ActionRun.domain
    is a plain string, so both shapes have to be tolerated here.
    """
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _job_title(job: OCRJob) -> str:
    return job.original_filename or f"{_v(job.job_type)} job"


def _job_objects(job: OCRJob) -> list[str]:
    # NB: source files can be shared between jobs (the /reuse flow points a new
    # job at an existing upload), so the shared-reference check in purge_expired
    # still applies — this only reports what the row references.
    return [k for k in (job.file_path, job.result_file_path) if k]


TRASH_TYPES: dict[str, TrashType] = {
    "job": TrashType(
        key="job",
        model=OCRJob,
        label="Extraction",
        title_of=_job_title,
        object_keys=_job_objects,
    ),
    "agent_run": TrashType(
        key="agent_run",
        model=AgentRun,
        label="Pipeline run",
        title_of=lambda r: f"{_v(r.domain)} · {_v(r.pipeline_type)}",
    ),
    "action_run": TrashType(
        key="action_run",
        model=ActionRun,
        label="Action run",
        title_of=lambda r: f"{_v(r.domain)} · {_v(r.action_type)}",
    ),
    "chat_session": TrashType(
        key="chat_session",
        model=ChatSession,
        label="Chat session",
        title_of=lambda s: s.title or "Untitled chat",
    ),
    "batch": TrashType(
        key="batch",
        model=BatchJob,
        label="Batch",
        title_of=lambda b: b.name or "Untitled batch",
    ),
}


class TrashError(Exception):
    """Raised for a bad type key, a missing row, or a wrong-state transition."""


def get_type(type_key: str) -> TrashType:
    t = TRASH_TYPES.get(type_key)
    if not t:
        raise TrashError(
            f"Unknown trash type '{type_key}'. Valid: {sorted(TRASH_TYPES)}"
        )
    return t


async def _owned_row(db: AsyncSession, t: TrashType, row_id: str, user_id: str):
    row = (await db.execute(
        select(t.model).where(t.model.id == row_id, t.model.user_id == user_id)
    )).scalar_one_or_none()
    if not row:
        raise TrashError(f"{t.label} not found.")
    return row


async def soft_delete(db: AsyncSession, type_key: str, row_id: str, user_id: str):
    """Move one row to Trash. Idempotent — re-deleting keeps the original time."""
    t = get_type(type_key)
    row = await _owned_row(db, t, row_id, user_id)

    if row.deleted_at is None:
        row.deleted_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("trash.deleted", type=type_key, id=row_id[:8], user_id=user_id[:8])
    return row


async def restore(db: AsyncSession, type_key: str, row_id: str, user_id: str):
    """Bring one row back out of Trash."""
    t = get_type(type_key)
    row = await _owned_row(db, t, row_id, user_id)

    if row.deleted_at is None:
        raise TrashError(f"This {t.label.lower()} is not in Trash.")

    row.deleted_at = None
    await db.commit()
    logger.info("trash.restored", type=type_key, id=row_id[:8], user_id=user_id[:8])
    return row


async def list_trash(
    db: AsyncSession,
    user_id: str,
    type_key: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Everything this user has in Trash, newest deletion first.

    Queried per type and merged in Python rather than as a SQL UNION: the
    tables share no common shape, the result set is bounded by `limit`, and
    keeping it explicit means adding a type later is one dict entry rather
    than a rewrite.
    """
    types = [get_type(type_key)] if type_key else list(TRASH_TYPES.values())
    items: list[dict] = []

    for t in types:
        rows = (await db.execute(
            select(t.model)
            .where(t.model.user_id == user_id, t.model.deleted_at.is_not(None))
            .order_by(t.model.deleted_at.desc())
            .limit(limit)
        )).scalars().all()

        for row in rows:
            deleted_at = row.deleted_at
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)
            expires_at = deleted_at + timedelta(days=RETENTION_DAYS)
            items.append({
                "type": t.key,
                "type_label": t.label,
                "id": row.id,
                "title": t.title_of(row),
                "deleted_at": deleted_at,
                "expires_at": expires_at,
                "days_remaining": max(0, (expires_at - datetime.now(timezone.utc)).days),
            })

    items.sort(key=lambda i: i["deleted_at"], reverse=True)
    return items[:limit]


async def trash_count(db: AsyncSession, user_id: str) -> int:
    total = 0
    for t in TRASH_TYPES.values():
        total += (await db.execute(
            select(func.count(t.model.id)).where(
                t.model.user_id == user_id, t.model.deleted_at.is_not(None)
            )
        )).scalar() or 0
    return total


async def _collect_and_delete(db: AsyncSession, t: TrashType, row) -> list[str]:
    """
    Physically remove one row, returning the object keys safe to delete.

    A source upload can be referenced by more than one job (the /reuse flow
    points a new job at an existing object), so an object is only removed
    once no surviving row still references it — otherwise purging one job
    would silently break another that is still live.
    """
    keys = t.object_keys(row)
    safe: list[str] = []

    for key in keys:
        if t.model is OCRJob:
            others = (await db.execute(
                select(func.count(OCRJob.id)).where(
                    OCRJob.file_path == key, OCRJob.id != row.id
                )
            )).scalar() or 0
            # result_file_path is unique per job; file_path may be shared.
            if key == row.file_path and others > 0:
                continue
        safe.append(key)

    await db.delete(row)
    return safe


async def purge_one(db: AsyncSession, type_key: str, row_id: str, user_id: str) -> None:
    """Delete one trashed row for good, including its objects."""
    t = get_type(type_key)
    row = await _owned_row(db, t, row_id, user_id)

    if row.deleted_at is None:
        raise TrashError(
            f"This {t.label.lower()} is not in Trash. Move it to Trash before deleting it permanently."
        )

    keys = await _collect_and_delete(db, t, row)
    await db.commit()

    if keys:
        await storage_service.delete_objects(keys)
    logger.info("trash.purged", type=type_key, id=row_id[:8], objects=len(keys))


async def empty_trash(db: AsyncSession, user_id: str) -> int:
    """Purge everything this user has in Trash. Returns how many rows went."""
    removed = 0
    all_keys: list[str] = []

    for t in TRASH_TYPES.values():
        rows = (await db.execute(
            select(t.model).where(
                t.model.user_id == user_id, t.model.deleted_at.is_not(None)
            )
        )).scalars().all()
        for row in rows:
            all_keys.extend(await _collect_and_delete(db, t, row))
            removed += 1

    await db.commit()
    if all_keys:
        await storage_service.delete_objects(all_keys)
    logger.info("trash.emptied", user_id=user_id[:8], removed=removed)
    return removed


async def purge_expired(db: AsyncSession) -> dict[str, int]:
    """
    Remove everything trashed longer than RETENTION_DAYS ago, for every user.

    Runs from the nightly beat schedule. Deliberately per-type and committed
    once at the end so a failure part-way leaves the DB consistent; object
    deletion happens only after that commit succeeds, so a crash can orphan
    an object but can never delete one still referenced by a live row.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    removed: dict[str, int] = {}
    all_keys: list[str] = []

    for t in TRASH_TYPES.values():
        rows = (await db.execute(
            select(t.model).where(
                t.model.deleted_at.is_not(None), t.model.deleted_at < cutoff
            )
        )).scalars().all()
        for row in rows:
            all_keys.extend(await _collect_and_delete(db, t, row))
        if rows:
            removed[t.key] = len(rows)

    if removed:
        await db.commit()
        if all_keys:
            await storage_service.delete_objects(all_keys)
        logger.info("trash.purge_expired", removed=removed, objects=len(all_keys))

    return removed
