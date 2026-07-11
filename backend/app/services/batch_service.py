"""
Batch processing service.

Flow per BatchJob:
  1. For each BatchItem: run OCR (same thread pool as single jobs)
  2. On OCR success: immediately run the configured domain agent
  3. Update BatchItem + parent BatchJob progress incrementally
  4. Dispatch webhook on completion

Design decisions:
  - asyncio.gather with semaphore bounds concurrency (default 4)
    avoids hammering Claude API or disk I/O simultaneously
  - BatchItem status updated atomically per file so UI polls are useful
  - BatchJob.status = partial when ≥1 item fails — not binary pass/fail
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.ocr_service import process_job
from app.services.agent_service import run_agent
from app.services.webhook_service import dispatch_event

logger = logging.getLogger(__name__)

# Max parallel Claude API calls per batch — tune via env later
_CONCURRENCY = 4


async def _process_single_item(
    batch_item_id: str,
    file_path: str,
    original_filename: str,
    domain: str,
    pipeline_type: str,
    user_instructions: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """
    OCR → Agent for one file. Returns result dict.
    Wrapped in semaphore to bound concurrent Claude API calls.
    """
    async with semaphore:
        logger.info(f"[batch_item:{batch_item_id[:8]}] starting file={original_filename}")

        # 1. OCR — run in executor (CPU-bound / blocking)
        loop = asyncio.get_running_loop()
        job_type = "pdf_extract" if original_filename.lower().endswith(".pdf") else "ocr_image"

        try:
            ocr_result = await loop.run_in_executor(None, process_job, job_type, file_path, {})
        except Exception as exc:
            return {
                "batch_item_id": batch_item_id,
                "success": False,
                "error": f"OCR failed: {exc}",
                "structured_result": None,
                "summary": None,
                "confidence": 0,
            }

        if ocr_result.get("error"):
            return {
                "batch_item_id": batch_item_id,
                "success": False,
                "error": f"OCR error: {ocr_result['error']}",
                "extracted_text": None,
                "structured_result": None,
                "summary": None,
                "confidence": 0,
            }

        extracted_text: str = ocr_result.get("text") or ""
        if not extracted_text.strip():
            return {
                "batch_item_id": batch_item_id,
                "success": False,
                "error": "OCR produced no text",
                "extracted_text": None,
                "structured_result": None,
                "summary": None,
                "confidence": 0,
            }

        # 2. Agent
        agent_result = await run_agent(domain, pipeline_type, extracted_text, user_instructions)

        if agent_result.get("error"):
            return {
                "batch_item_id": batch_item_id,
                "success": False,
                "error": f"Agent error: {agent_result['error']}",
                "extracted_text": extracted_text[:2000],
                "structured_result": None,
                "summary": None,
                "confidence": 0,
            }

        logger.info(f"[batch_item:{batch_item_id[:8]}] done confidence={agent_result.get('confidence')}%")
        return {
            "batch_item_id": batch_item_id,
            "success": True,
            "error": None,
            "extracted_text": extracted_text[:2000],
            "structured_result": agent_result["structured_result"],
            "summary": agent_result["summary"],
            "confidence": agent_result["confidence"],
            "processing_time_ms": agent_result.get("processing_time_ms"),
        }


async def run_batch_job(batch_job_id: str, user_id: str) -> None:
    """
    Background task: execute all items in a BatchJob.

    Imports DB session locally — this runs outside the request lifecycle.
    """
    from app.db.database import AsyncSessionLocal
    from app.models.models import BatchJob, BatchItem, BatchStatus, AgentRun, AgentStatus, AgentDomain, OCRJob, JobStatus, JobType

    async with AsyncSessionLocal() as db:
        batch = await db.get(BatchJob, batch_job_id)
        if not batch:
            logger.error(f"[batch:{batch_job_id[:8]}] not found in DB")
            return

        items = (
            await db.execute(
                __import__("sqlalchemy", fromlist=["select"]).select(BatchItem)
                .where(BatchItem.batch_job_id == batch_job_id)
            )
        ).scalars().all()

        if not items:
            batch.status = BatchStatus.completed
            batch.completed_at = datetime.utcnow()
            await db.commit()
            return

        from sqlalchemy import select as sa_select

        # Update batch to processing
        batch.status = BatchStatus.processing
        batch.total_files = len(items)
        await db.commit()

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    # Build coroutines
    tasks = [
        _process_single_item(
            batch_item_id=item.id,
            file_path=item.file_path,
            original_filename=item.original_filename,
            domain=batch.domain.value,
            pipeline_type=batch.pipeline_type,
            user_instructions=batch.user_instructions or "",
            semaphore=semaphore,
        )
        for item in items
    ]

    # Execute all — gather preserves order, exceptions caught internally
    results = await asyncio.gather(*tasks, return_exceptions=True)

    completed = 0
    failed = 0

    async with AsyncSessionLocal() as db:
        batch = await db.get(BatchJob, batch_job_id)

        for item, result in zip(items, results):
            item_db = await db.get(BatchItem, item.id)
            if not item_db:
                continue

            if isinstance(result, Exception):
                # Unexpected exception from gather
                item_db.status = BatchStatus.failed
                item_db.error_message = str(result)
                item_db.completed_at = datetime.utcnow()
                failed += 1
                continue

            item_db.completed_at = datetime.utcnow()

            if result["success"]:
                item_db.status = BatchStatus.completed
                completed += 1

                # Create an AgentRun record per successful item
                agent_run = AgentRun(
                    user_id=user_id,
                    domain=batch.domain,
                    pipeline_type=batch.pipeline_type,
                    status=AgentStatus.completed,
                    input_text=result.get("extracted_text"),
                    structured_result=result["structured_result"],
                    summary=result["summary"],
                    confidence_score=result["confidence"],
                    processing_time_ms=result.get("processing_time_ms"),
                    original_filename=item.original_filename,
                    completed_at=datetime.utcnow(),
                )
                db.add(agent_run)
            else:
                item_db.status = BatchStatus.failed
                item_db.error_message = result.get("error", "Unknown error")
                failed += 1

        # Update BatchJob aggregate counters
        if batch:
            batch.completed_files = completed
            batch.failed_files = failed

            if failed == 0:
                batch.status = BatchStatus.completed
            elif completed == 0:
                batch.status = BatchStatus.failed
            else:
                batch.status = BatchStatus.partial  # mixed results

            batch.completed_at = datetime.utcnow()

        await db.commit()

        logger.info(
            f"[batch:{batch_job_id[:8]}] done "
            f"total={len(items)} completed={completed} failed={failed}"
        )

    # Dispatch webhook if configured
    try:
        await dispatch_event(
            user_id=user_id,
            event="batch.completed",
            payload={
                "batch_job_id": batch_job_id,
                "total": len(items),
                "completed": completed,
                "failed": failed,
                "status": batch.status.value,
            },
        )
    except Exception as exc:
        logger.warning(f"[batch:{batch_job_id[:8]}] webhook dispatch failed: {exc}")