"""
Celery task definitions for TextLens.

Track 2 additions:
  - publish_event() called after DB commit → pushes SSE event to frontend
  - send_*_email() called after DB commit → Resend email notification
  - Both are fire-and-forget (errors logged, never raised)

Task registry:
  process_ocr_job(job_id, extra_data)
  process_agent_run(run_id, domain, pipeline_type, extracted_text, instructions)
  check_and_dispatch_schedules  — beat, every 60 s
  process_scheduled_batch(schedule_id)
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.services.sse_service import publish_event as _publish_sse
from app.services.email_service import (
    send_job_notification as _send_job_email,
    send_agent_notification as _send_agent_email,
)

logger = structlog.get_logger(__name__)


# Asyncio bridge 
def _run_async(coro):
    """Run an async coroutine from a synchronous Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# Task: OCR processing 
from app.worker.celery_app import celery_app

@celery_app.task(
    name="app.worker.tasks.process_ocr_job",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    queue="ocr",
)
def process_ocr_job(self, job_id: str, extra_data: dict | None = None) -> dict:
    """
    Full OCR pipeline for a single OCRJob:
      1. Fetch job record from DB
      2. Download source file from MinIO to temp
      3. Run OCR in thread pool (extra_data forwarded — supports pdf_qa question)
      4. If result file produced (docx): upload to MinIO
      5. Persist result + status to DB
      6. Publish SSE event
      7. Send email notification (if RESEND_API_KEY is set)
      8. Fire webhook
    """
    return _run_async(_run_ocr_job(job_id, extra_data or {}))


async def _run_ocr_job(job_id: str, extra_data: dict | None = None) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import select

    from app.db.database import AsyncSessionLocal
    from app.models.models import OCRJob, JobStatus, WebhookEvent, User
    from app.services import storage_service
    from app.services.ocr_service import process_job
    from app.services.webhook_service import fire_webhook

    log = logger.bind(job_id=job_id[:8], task="process_ocr_job")
    tmp_input: str | None = None
    tmp_result: str | None = None

    try:
        # 1. Fetch job 
        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, job_id)
            if not job:
                log.error("job.not_found")
                return {"error": "job not found"}
            if not job.file_path:
                log.error("job.no_file_path")
                return {"error": "no file_path on job"}

            object_key = job.file_path
            job_type = job.job_type.value
            user_id = job.user_id
            original_filename = job.original_filename

        log.info("job.started", object_key=object_key, job_type=job_type)

        # 2. Download source from MinIO 
        ext = Path(original_filename).suffix or ".bin"
        tmp_input = await storage_service.download_to_temp(object_key, suffix=ext)

        # 3. Run OCR in thread pool (CPU-bound / blocking) 
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr") as pool:
            ocr_result: dict = await loop.run_in_executor(
                pool, process_job, job_type, tmp_input, extra_data or {}
            )

        log.info(
            "job.ocr_done",
            processing_time_ms=ocr_result.get("processing_time_ms"),
            has_error=bool(ocr_result.get("error")),
        )

        # 4. Upload result file (docx etc.) to MinIO 
        result_key: str | None = None
        local_result_path: str | None = ocr_result.get("file_path")

        if local_result_path and os.path.exists(local_result_path):
            result_filename = Path(local_result_path).name
            result_key = storage_service.build_result_key(user_id, job_id, result_filename)
            content_type = storage_service.content_type_for(result_filename)
            await storage_service.upload_file(local_result_path, result_key, content_type)
            tmp_result = local_result_path

        # 5. Persist result to DB 
        final_status = JobStatus.failed if ocr_result.get("error") else JobStatus.completed

        async with AsyncSessionLocal() as db:
            job = await db.get(OCRJob, job_id)
            user = await db.get(User, user_id)
            if job:
                job.status = final_status
                job.result_text = ocr_result.get("text")
                job.result_file_path = result_key
                job.error_message = ocr_result.get("error")
                job.page_count = ocr_result.get("page_count")
                job.processing_time_ms = ocr_result.get("processing_time_ms")
                job.completed_at = datetime.utcnow()
                await db.commit()
                log.info("job.saved", status=job.status.value)

        # 6a. Trigger RAG ingestion if text was extracted 
        if final_status == JobStatus.completed and ocr_result.get("text"):
            ingest_document.apply_async(
                args=[job_id],
                countdown=2,   # slight delay so DB commit is visible to the worker
            )
            log.info("job.ingest_queued", job_id=job_id[:8])

        # 6b. Publish SSE event (sync — safe here) 
        _publish_sse(user_id, "job_update", {
            "job_id": job_id,
            "status": final_status.value,
            "job_type": job_type,
            "original_filename": original_filename,
            "page_count": ocr_result.get("page_count"),
            "processing_time_ms": ocr_result.get("processing_time_ms"),
            "result_file_path": result_key,
            "error_message": ocr_result.get("error"),
        })

        # 7. Send email notification 
        if user:
            _send_job_email(
                to_email=user.email,
                user_name=user.full_name or user.email.split("@")[0],
                filename=original_filename,
                job_type=job_type,
                status=final_status.value,
                processing_time_ms=ocr_result.get("processing_time_ms"),
                error_message=ocr_result.get("error"),
            )

        # 8. Fire webhook 
        wh_event = (
            WebhookEvent.job_failed if ocr_result.get("error")
            else WebhookEvent.job_completed
        )
        await fire_webhook(user_id, wh_event, {
            "job_id": job_id,
            "job_type": job_type,
            "status": final_status.value,
            "original_filename": original_filename,
            "page_count": ocr_result.get("page_count"),
            "processing_time_ms": ocr_result.get("processing_time_ms"),
            "error_message": ocr_result.get("error"),
        })

        return {"job_id": job_id, "status": final_status.value}

    except Exception as exc:
        log.error("job.crashed", error=str(exc), exc_info=True)
        try:
            async with AsyncSessionLocal() as db:
                job = await db.get(OCRJob, job_id)
                if job:
                    job.status = JobStatus.failed
                    job.error_message = f"{type(exc).__name__}: {exc}"
                    job.completed_at = datetime.utcnow()
                    await db.commit()
            # Still push SSE failure so UI stops spinning
            _publish_sse(user_id, "job_update", {
                "job_id": job_id,
                "status": "failed",
                "error_message": str(exc),
            })
        except Exception:
            pass
        raise

    finally:
        for tmp in (tmp_input, tmp_result):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# Task: Agent run 
@celery_app.task(
    name="app.worker.tasks.process_agent_run",
    bind=True,
    max_retries=1,
    default_retry_delay=10,
    queue="agents",
)
def process_agent_run(
    self,
    run_id: str,
    domain: str,
    pipeline_type: str,
    extracted_text: str,
    instructions: str,
) -> dict:
    """
    Run a domain agent pipeline via Claude API:
      1. Call agent_service.run_agent()
      2. Persist structured_result to DB
      3. Publish SSE event
      4. Send email notification
      5. Fire webhook
    """
    return _run_async(_run_agent(run_id, domain, pipeline_type, extracted_text, instructions))


async def _run_agent(
    run_id: str,
    domain: str,
    pipeline_type: str,
    extracted_text: str,
    instructions: str,
) -> dict:
    from app.db.database import AsyncSessionLocal
    from app.models.models import AgentRun, AgentStatus, WebhookEvent, User
    from app.services.agent_service import run_agent
    from app.services.webhook_service import fire_webhook

    log = logger.bind(run_id=run_id[:8], domain=domain, pipeline=pipeline_type, task="process_agent_run")
    log.info("agent.started")

    try:
        result = await run_agent(domain, pipeline_type, extracted_text, instructions)
        final_status = AgentStatus.failed if result.get("error") else AgentStatus.completed

        user_id: str | None = None
        original_filename: str | None = None

        async with AsyncSessionLocal() as db:
            run = await db.get(AgentRun, run_id)
            user = await db.get(User, run.user_id) if run else None

            if run:
                user_id = run.user_id
                original_filename = run.original_filename
                run.status = final_status
                run.structured_result = result.get("structured_result")
                run.summary = result.get("summary")
                run.confidence_score = result.get("confidence")
                run.error_message = result.get("error")
                run.processing_time_ms = result.get("processing_time_ms")
                run.completed_at = datetime.utcnow()
                await db.commit()
                log.info("agent.saved", status=run.status.value)

        if not user_id:
            return {"run_id": run_id, "status": "skipped_no_run"}

        # Publish SSE event 
        _publish_sse(user_id, "agent_update", {
            "run_id": run_id,
            "domain": domain,
            "pipeline_type": pipeline_type,
            "status": final_status.value,
            "original_filename": original_filename,
            "confidence_score": result.get("confidence"),
            "processing_time_ms": result.get("processing_time_ms"),
            "error_message": result.get("error"),
        })

        # Send email 
        if user:
            _send_agent_email(
                to_email=user.email,
                user_name=user.full_name or user.email.split("@")[0],
                filename=original_filename or "document",
                domain=domain,
                pipeline_type=pipeline_type,
                status=final_status.value,
                confidence_score=result.get("confidence"),
                processing_time_ms=result.get("processing_time_ms"),
                error_message=result.get("error"),
            )

        # Fire webhook 
        await fire_webhook(user_id, WebhookEvent.agent_completed, {
            "run_id": run_id,
            "domain": domain,
            "pipeline_type": pipeline_type,
            "status": final_status.value,
            "original_filename": original_filename,
            "confidence_score": result.get("confidence"),
            "processing_time_ms": result.get("processing_time_ms"),
            "error_message": result.get("error"),
        })

        return {"run_id": run_id, "status": final_status.value}

    except Exception as exc:
        log.error("agent.crashed", error=str(exc), exc_info=True)
        try:
            async with AsyncSessionLocal() as db:
                run = await db.get(AgentRun, run_id)
                if run:
                    run.status = AgentStatus.failed
                    run.error_message = f"{type(exc).__name__}: {exc}"
                    run.completed_at = datetime.utcnow()
                    await db.commit()
                    _publish_sse(run.user_id, "agent_update", {
                        "run_id": run_id,
                        "status": "failed",
                        "error_message": str(exc),
                    })
        except Exception:
            pass
        raise


# Task: Scheduled batch dispatch 
@celery_app.task(name="app.worker.tasks.check_and_dispatch_schedules", bind=True)
def check_and_dispatch_schedules(self):
    """Beat task — fires every 60 s. Finds due ScheduledBatches and enqueues them."""
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
                ScheduledBatch.is_active.is_(True),
                ScheduledBatch.next_run_at <= now,
            )
        )
        due = res.scalars().all()

        for schedule in due:
            logger.info("schedule.dispatching", schedule_id=schedule.id[:8], name=schedule.name)
            process_scheduled_batch.delay(schedule.id)
            schedule.last_run_at = now
            schedule.next_run_at = _calc_next_run(schedule.cron_expr)
            schedule.run_count = (schedule.run_count or 0) + 1
            db.add(schedule)
            dispatched += 1

        if dispatched:
            await db.commit()

    return {"dispatched": dispatched}


def _calc_next_run(cron_expr: str):
    from croniter import croniter
    return croniter(cron_expr, datetime.now(timezone.utc).replace(tzinfo=None)).get_next(datetime)


# Task: Scheduled batch execution 
@celery_app.task(
    name="app.worker.tasks.process_scheduled_batch",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def process_scheduled_batch(self, schedule_id: str):
    """Pull files from Drive (if configured), create BatchJob, process all files."""
    return _run_async(_run_scheduled_batch(schedule_id))


async def _run_scheduled_batch(schedule_id: str):
    from app.db.database import AsyncSessionLocal
    from app.models.models import ScheduledBatch, BatchJob, BatchItem, BatchStatus, AgentDomain
    from app.services.batch_service import run_batch_job

    async with AsyncSessionLocal() as db:
        schedule = await db.get(ScheduledBatch, schedule_id)
        if not schedule:
            logger.error("scheduled_batch.not_found", schedule_id=schedule_id)
            return {"error": "schedule not found"}

        file_paths: list[tuple[str, str]] = []

        if schedule.drive_folder_id:
            try:
                from app.services.drive_service import list_folder_files, download_file_to_storage
                files = await list_folder_files(schedule.drive_folder_id)
                for f in files:
                    key = await download_file_to_storage(f["id"], f["name"], schedule.user_id)
                    file_paths.append((key, f["name"]))
            except Exception as exc:
                logger.error("scheduled_batch.drive_error", error=str(exc))

        if not file_paths:
            logger.warning("scheduled_batch.no_files", schedule_id=schedule_id[:8])
            return {"status": "no_files"}

        batch_job = BatchJob(
            user_id=schedule.user_id,
            name=f"[Scheduled] {schedule.name}",
            domain=AgentDomain(schedule.domain),
            pipeline_type=schedule.pipeline_type,
            status=BatchStatus.processing,
            total_files=len(file_paths),
            user_instructions=schedule.user_instructions,
        )
        db.add(batch_job)
        await db.flush()

        items = []
        for object_key, filename in file_paths:
            item = BatchItem(
                batch_job_id=batch_job.id,
                original_filename=filename,
                file_path=object_key,
                status=BatchStatus.pending,
            )
            db.add(item)
            items.append(item)

        await db.flush()
        batch_id = batch_job.id
        item_ids = [i.id for i in items]
        await db.commit()

    await run_batch_job(batch_id, item_ids)
    return {"batch_id": batch_id, "files": len(file_paths)}


# Task: Document ingestion (RAG) 
@celery_app.task(
    name="app.worker.tasks.ingest_document",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="ingest",
)
def ingest_document(self, job_id: str) -> dict:
    """
    Chunk + embed a completed OCR job's extracted text and store vectors
    in the document_chunks table for RAG retrieval.

    Steps:
      1. Fetch result_text from DB
      2. Chunk with chunking_service
      3. Embed all chunks with Voyage AI (batched, input_type=document)
      4. Delete any existing chunks for this job (idempotent re-ingestion)
      5. Bulk-insert new chunks with embedding + tsvector

    Triggered automatically by _run_ocr_job() after OCR completes.
    Can also be called manually to re-index a job:
        from app.worker.tasks import ingest_document
        ingest_document.delay(job_id)
    """
    return _run_async(_run_ingest(job_id))


async def _run_ingest(job_id: str) -> dict:
    import json as _json
    import uuid as _uuid
    from sqlalchemy import text as _text

    from app.db.database import AsyncSessionLocal
    from app.models.models import OCRJob, JobStatus
    from app.services.chunking_service import chunk_document
    from app.services.embedding_service import embed_documents, vec_to_pg_str

    log = logger.bind(job_id=job_id[:8], task="ingest_document")

    # 1. Fetch job 
    async with AsyncSessionLocal() as db:
        job = await db.get(OCRJob, job_id)
        if not job:
            log.error("ingest.job_not_found")
            return {"error": "job not found"}
        if job.status != JobStatus.completed:
            log.warning("ingest.job_not_complete", status=job.status.value)
            return {"skipped": "job not completed", "status": job.status.value}
        if not job.result_text or not job.result_text.strip():
            log.warning("ingest.no_text")
            return {"skipped": "no result_text"}
        text    = job.result_text
        user_id = job.user_id

    log.info("ingest.started", text_len=len(text))

    # 2. Chunk 
    chunks = chunk_document(text)
    if not chunks:
        log.warning("ingest.no_chunks")
        return {"chunks": 0}

    log.info("ingest.chunked", count=len(chunks))

    # 3. Embed (batched via Voyage AI) 
    texts_to_embed = [c["content"] for c in chunks]
    embeddings     = await embed_documents(texts_to_embed)

    log.info("ingest.embedded", count=len(embeddings))

    # 4 + 5. Upsert — delete old chunks then bulk insert new ones 
    async with AsyncSessionLocal() as db:
        # Delete existing (idempotent re-ingestion)
        await db.execute(
            _text("DELETE FROM document_chunks WHERE job_id = :job_id"),
            {"job_id": job_id},
        )

        # Bulk insert with embedding vector + tsvector in one pass
        for chunk, embedding in zip(chunks, embeddings):
            vec_str = vec_to_pg_str(embedding)
            await db.execute(
                _text("""
                    INSERT INTO document_chunks
                        (id, job_id, user_id, chunk_index, content, token_count,
                         embedding, ts_vector, chunk_metadata, created_at)
                    VALUES
                        (:id, :job_id, :user_id, :chunk_index, :content, :token_count,
                         :vec::vector,
                         to_tsvector('english', :content),
                         :metadata, NOW())
                """),
                {
                    "id":          str(_uuid.uuid4()),
                    "job_id":      job_id,
                    "user_id":     user_id,
                    "chunk_index": chunk["chunk_index"],
                    "content":     chunk["content"],
                    "token_count": chunk["token_count"],
                    "vec":         vec_str,
                    "metadata":    _json.dumps(chunk.get("metadata", {})),
                },
            )

        await db.commit()

    log.info("ingest.complete", chunks_stored=len(chunks))
    return {"job_id": job_id, "chunks": len(chunks)}