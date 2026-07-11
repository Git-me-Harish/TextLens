"""
Celery tasks for TextLens.

check_and_dispatch_schedules — runs every 60s via beat, finds due ScheduledBatches,
  enqueues process_scheduled_batch for each.

process_scheduled_batch — pulls files from Drive folder, creates BatchJob,
  processes all files through the configured pipeline.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── Beat task — fires every 60s ──────────────────────────────────────

@shared_task(name="app.worker.tasks.check_and_dispatch_schedules", bind=True)
def check_and_dispatch_schedules(self):
    """Find all active schedules whose next_run_at <= now and enqueue them."""
    return _run_async(_check_schedules_async())


async def _check_schedules_async():
    from sqlalchemy import select
    from app.db.database import AsyncSessionLocal
    from app.models.models import ScheduledBatch

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dispatched = 0

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ScheduledBatch).where(
                ScheduledBatch.is_active == True,
                ScheduledBatch.next_run_at <= now,
            )
        )
        due = res.scalars().all()

        for schedule in due:
            logger.info(f"[beat] dispatching schedule {schedule.id[:8]} ({schedule.name})")
            process_scheduled_batch.delay(schedule.id)

            # Update next_run_at immediately so we don't double-dispatch
            schedule.last_run_at = now
            schedule.next_run_at = _calc_next_run(schedule.cron_expr)
            schedule.run_count = (schedule.run_count or 0) + 1
            db.add(schedule)
            dispatched += 1

        if dispatched:
            await db.commit()

    return {"dispatched": dispatched}


# ── Main batch task ───────────────────────────────────────────────────

@shared_task(
    name="app.worker.tasks.process_scheduled_batch",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def process_scheduled_batch(self, schedule_id: str):
    """Pull files from Drive, create BatchJob, process all files."""
    return _run_async(_run_scheduled_batch(schedule_id))


async def _run_scheduled_batch(schedule_id: str):
    from sqlalchemy import select
    from app.db.database import AsyncSessionLocal
    from app.models.models import ScheduledBatch, BatchJob, BatchItem, BatchStatus, AgentDomain
    from app.services.batch_service import run_batch_job

    async with AsyncSessionLocal() as db:
        schedule = await db.get(ScheduledBatch, schedule_id)
        if not schedule:
            logger.error(f"[task] schedule {schedule_id} not found")
            return {"error": "schedule not found"}

        # Pull file list from Drive if folder configured
        file_ids = []
        if schedule.drive_folder_id:
            try:
                file_ids = await _list_drive_folder(schedule.drive_folder_id)
                logger.info(f"[task] drive folder yielded {len(file_ids)} files")
            except Exception as exc:
                logger.error(f"[task] drive folder fetch failed: {exc}")

        if not file_ids:
            logger.info(f"[task] no files found for schedule {schedule_id[:8]}, skipping")
            return {"files": 0}

        # Download files and create BatchJob
        try:
            domain_enum = AgentDomain(schedule.domain)
        except ValueError:
            domain_enum = AgentDomain.general

        batch = BatchJob(
            user_id=schedule.user_id,
            name=f"{schedule.name} — {datetime.utcnow().strftime('%Y-%m-%d')}",
            domain=domain_enum,
            pipeline_type=schedule.pipeline_type,
            status=BatchStatus.pending,
            total_files=len(file_ids),
            user_instructions=schedule.user_instructions,
        )
        db.add(batch)
        await db.flush()
        await db.refresh(batch)

        items = []
        for fid in file_ids:
            item = BatchItem(
                batch_job_id=batch.id,
                original_filename=fid.get("name", fid.get("id", "unknown")),
                file_path=fid.get("local_path", ""),
                status=BatchStatus.pending,
            )
            db.add(item)
            items.append(item)

        await db.commit()
        await db.refresh(batch)

        # Hand off to existing batch processing pipeline
        asyncio.ensure_future(run_batch_job(batch.id, schedule.user_id))
        logger.info(f"[task] batch {batch.id[:8]} created with {len(items)} items")

        return {"batch_id": batch.id, "files": len(items)}


async def _list_drive_folder(folder_id: str) -> list[dict]:
    """Use Drive MCP via Claude to list files in a folder and download them."""
    import json, uuid, base64
    from pathlib import Path
    import httpx
    from app.core.config import settings

    if not settings.ANTHROPIC_API_KEY:
        return []

    prompt = (
        f"List all PDF and image files in Google Drive folder ID: {folder_id}. "
        "For each file return id, name, mimeType. "
        "Return ONLY a JSON array, no markdown."
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "mcp-client-2025-04-04",
                "x-api-key": settings.ANTHROPIC_API_KEY,
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
                "mcp_servers": [{"type": "url", "url": "https://drivemcp.googleapis.com/mcp/v1", "name": "gdrive"}],
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API error: {resp.status_code}")

    data = resp.json()
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "mcp_tool_result":
            c = block.get("content", [])
            text = c[0].get("text", "") if c else ""
            break
        if block.get("type") == "text":
            text = block.get("text", "")

    text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        files = json.loads(text)
    except Exception:
        return []

    # Download each file to disk
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    result = []

    for f in (files if isinstance(files, list) else []):
        try:
            dl_resp = await _download_drive_file(f["id"], f["name"])
            if dl_resp:
                result.append({**f, "local_path": dl_resp})
        except Exception as exc:
            logger.warning(f"[task] could not download {f.get('name')}: {exc}")

    return result


async def _download_drive_file(file_id: str, filename: str) -> str | None:
    """Download a single Drive file, return local path."""
    import base64, uuid
    from pathlib import Path
    import httpx
    from app.core.config import settings

    prompt = (
        f"Download Google Drive file ID: {file_id} as base64. "
        "Return ONLY the base64 string."
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "mcp-client-2025-04-04",
                "x-api-key": settings.ANTHROPIC_API_KEY,
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
                "mcp_servers": [{"type": "url", "url": "https://drivemcp.googleapis.com/mcp/v1", "name": "gdrive"}],
            },
        )

    if resp.status_code != 200:
        return None

    data = resp.json()
    b64 = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            b64 = block["text"].strip()
            break

    if not b64:
        return None

    try:
        file_bytes = base64.b64decode(b64)
    except Exception:
        return None

    path = Path(settings.UPLOAD_DIR) / f"{uuid.uuid4()}_{filename}"
    path.write_bytes(file_bytes)
    return str(path)


# ── Cron helper ──────────────────────────────────────────────────────

def _calc_next_run(cron_expr: str) -> datetime:
    """Parse cron expression and return next UTC datetime."""
    try:
        from croniter import croniter
        ci = croniter(cron_expr, datetime.utcnow())
        return ci.get_next(datetime)
    except Exception:
        # Fallback: next day if croniter unavailable or bad expr
        from datetime import timedelta
        return datetime.utcnow() + timedelta(days=1)