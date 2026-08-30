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
from app.services.email_service import (
    send_agent_notification as _send_agent_email,
)
from app.services.email_service import (
    send_job_notification as _send_job_email,
)
from app.services.sse_service import publish_event as _publish_sse

logger = structlog.get_logger(__name__)


# asyncio bridge
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
    # Bounds worst-case worker-slot lockup from a pathological file (huge
    # page count, a Tesseract hang on a corrupted image, etc.) — every other
    # task family in this codebase sets one of these; this one never had it.
    # Generous enough for a large multi-page scanned PDF at 300dpi with
    # multiple PSM passes per page (see ocr_service.py's ocr_image_file).
    # NB: the per-task decorator kwargs are time_limit/soft_time_limit —
    # NOT task_time_limit/task_soft_time_limit (that prefixed form is only
    # for celery_app.conf.update(), and silently does nothing here).
    time_limit=600,
    soft_time_limit=540,
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

    from app.db.database import AsyncSessionLocal
    from app.models.models import JobStatus, OCRJob, User, WebhookEvent
    from app.services import storage_service
    from app.services.notification_service import create_notification, format_job_notification
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

        # 3. Resolve multi-file extra keys for Studio operations
        # pdf_merge and images_to_pdf pass extra MinIO keys that must be
        # downloaded to temp files before process_job can use them.
        resolved_extra = dict(extra_data or {})

        if "input_paths_keys" in resolved_extra:
            extra_tmp_paths = []
            for key in resolved_extra.pop("input_paths_keys"):
                tmp = await storage_service.download_to_temp(key, suffix=".pdf")
                extra_tmp_paths.append(tmp)
            resolved_extra["input_paths"] = extra_tmp_paths

        if "image_paths_keys" in resolved_extra:
            extra_tmp_paths = []
            for key in resolved_extra.pop("image_paths_keys"):
                ext = Path(key).suffix or ".jpg"
                tmp = await storage_service.download_to_temp(key, suffix=ext)
                extra_tmp_paths.append(tmp)
            resolved_extra["image_paths"] = extra_tmp_paths

        # 4. Run OCR in thread pool (CPU-bound / blocking)
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr") as pool:
            ocr_result: dict = await loop.run_in_executor(
                pool, process_job, job_type, tmp_input, resolved_extra
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
            result_key = storage_service.build_result_key(
                user_id, job_id, result_filename
            )
            content_type = storage_service.content_type_for(result_filename)
            await storage_service.upload_file(
                local_result_path, result_key, content_type
            )
            tmp_result = local_result_path

        # 5. Persist result to DB
        final_status = (
            JobStatus.failed if ocr_result.get("error") else JobStatus.completed
        )

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
                job.ocr_confidence = ocr_result.get("ocr_confidence")
                job.completed_at = datetime.utcnow()
                await db.commit()
                log.info("job.saved", status=job.status.value)

        # 6a. Trigger RAG ingestion — only for job types whose result_text is
        # genuine document content. Document Studio operations (split, merge,
        # compress, images→PDF, pdf→word) set result_text to a short status
        # string like "Extracted pages 2–3 (2 page(s))" — truthy, but not
        # something worth chunking/embedding, and ingesting it wasted API
        # calls. pdf_qa's result_text is a Q&A answer, not the document
        # either, so it's excluded too.
        # Dispatched with no countdown — the job's status/result_text was
        # already committed above, so it's visible to the DB immediately;
        # no delay needed. A countdown here previously routed through
        # Celery's timer/ETA scheduling path instead of immediate dispatch,
        # which (reproduced live) executes the task against an asyncpg
        # connection bound to a different event loop than the one
        # _run_async() thinks is current — "attached to a different loop" —
        # crashing ingest_document and, worse, corrupting the shared engine's
        # connection pool badly enough that the NEXT unrelated task on this
        # worker would then fail too ("another operation is in progress").
        _INGESTIBLE_JOB_TYPES = {"ocr_image", "pdf_extract", "pdf_summarize", "pdf_to_markdown"}
        if final_status == JobStatus.completed and ocr_result.get("text") and job_type in _INGESTIBLE_JOB_TYPES:
            ingest_document.delay(job_id)
            log.info("job.ingest_queued", job_id=job_id[:8])

        # 6b. Persist a notification + publish SSE event
        title, message = format_job_notification(
            final_status.value, job_type, original_filename, ocr_result.get("error")
        )
        async with AsyncSessionLocal() as db:
            notif = await create_notification(
                db, user_id, type="job", status=final_status.value,
                title=title, message=message, link="/history",
                entity_type="ocr_job", entity_id=job_id,
            )

        _publish_sse(
            user_id,
            "job_update",
            {
                "job_id": job_id,
                "status": final_status.value,
                "job_type": job_type,
                "original_filename": original_filename,
                "page_count": ocr_result.get("page_count"),
                "processing_time_ms": ocr_result.get("processing_time_ms"),
                "result_file_path": result_key,
                "error_message": ocr_result.get("error"),
                "notification": notif,
            },
        )

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
            WebhookEvent.job_failed
            if ocr_result.get("error")
            else WebhookEvent.job_completed
        )
        await fire_webhook(
            user_id,
            wh_event,
            {
                "job_id": job_id,
                "job_type": job_type,
                "status": final_status.value,
                "original_filename": original_filename,
                "page_count": ocr_result.get("page_count"),
                "processing_time_ms": ocr_result.get("processing_time_ms"),
                "error_message": ocr_result.get("error"),
            },
        )

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
            # Deliberately doesn't reference original_filename — this crash
            # handler can run before that variable is ever assigned (e.g. the
            # job lookup itself failed), so a generic phrase avoids a NameError
            # inside an already-failing exception handler.
            async with AsyncSessionLocal() as notif_db:
                notif = await create_notification(
                    notif_db, user_id, type="job", status="failed",
                    title="Extraction failed",
                    message=f"Job {job_id[:8]} could not be processed: {str(exc)[:200]}",
                    link="/history", entity_type="ocr_job", entity_id=job_id,
                )
            _publish_sse(
                user_id,
                "job_update",
                {
                    "job_id": job_id,
                    "status": "failed",
                    "error_message": str(exc),
                    "notification": notif,
                },
            )
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
    return _run_async(
        _run_agent(run_id, domain, pipeline_type, extracted_text, instructions)
    )


async def _run_agent(
    run_id: str,
    domain: str,
    pipeline_type: str,
    extracted_text: str,
    instructions: str,
) -> dict:
    from app.db.database import AsyncSessionLocal
    from app.models.models import AgentRun, AgentStatus, User, WebhookEvent
    from app.services.agent_service import run_agent
    from app.services.notification_service import create_notification, format_agent_notification
    from app.services.webhook_service import fire_webhook

    log = logger.bind(
        run_id=run_id[:8],
        domain=domain,
        pipeline=pipeline_type,
        task="process_agent_run",
    )
    log.info("agent.started")

    try:
        result = await run_agent(domain, pipeline_type, extracted_text, instructions)
        final_status = (
            AgentStatus.failed if result.get("error") else AgentStatus.completed
        )

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

        # Persist a notification, then publish SSE event
        title, message = format_agent_notification(
            final_status.value, domain, pipeline_type,
            original_filename or "document", result.get("error"),
        )
        async with AsyncSessionLocal() as notif_db:
            notif = await create_notification(
                notif_db, user_id, type="agent", status=final_status.value,
                title=title, message=message, link="/agent-history",
                entity_type="agent_run", entity_id=run_id,
            )

        _publish_sse(
            user_id,
            "agent_update",
            {
                "run_id": run_id,
                "domain": domain,
                "pipeline_type": pipeline_type,
                "status": final_status.value,
                "original_filename": original_filename,
                "confidence_score": result.get("confidence"),
                "processing_time_ms": result.get("processing_time_ms"),
                "error_message": result.get("error"),
                "notification": notif,
            },
        )

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
        await fire_webhook(
            user_id,
            WebhookEvent.agent_completed,
            {
                "run_id": run_id,
                "domain": domain,
                "pipeline_type": pipeline_type,
                "status": final_status.value,
                "original_filename": original_filename,
                "confidence_score": result.get("confidence"),
                "processing_time_ms": result.get("processing_time_ms"),
                "error_message": result.get("error"),
            },
        )

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
                    async with AsyncSessionLocal() as notif_db:
                        notif = await create_notification(
                            notif_db, run.user_id, type="agent", status="failed",
                            title="Pipeline run failed",
                            message=f"Run {run_id[:8]} could not be completed: {str(exc)[:200]}",
                            link="/agent-history", entity_type="agent_run", entity_id=run_id,
                        )
                    _publish_sse(
                        run.user_id,
                        "agent_update",
                        {
                            "run_id": run_id,
                            "status": "failed",
                            "error_message": str(exc),
                            "notification": notif,
                        },
                    )
        except Exception:
            pass
        raise


# Task: Scheduled batch dispatch
@celery_app.task(name="app.worker.tasks.check_and_dispatch_schedules", bind=True)
def check_and_dispatch_schedules(self):
    """Beat task — fires every 60 s. Finds due ScheduledBatches and enqueues them."""
    return _run_async(_check_schedules_async())


async def _check_schedules_async():
    from app.db.database import AsyncSessionLocal
    from app.models.models import ScheduledBatch
    from sqlalchemy import select

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
            logger.info(
                "schedule.dispatching", schedule_id=schedule.id[:8], name=schedule.name
            )
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

    return croniter(
        cron_expr, datetime.now(timezone.utc).replace(tzinfo=None)
    ).get_next(datetime)


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
    from app.models.models import (
        AgentDomain,
        BatchItem,
        BatchJob,
        BatchStatus,
        ScheduledBatch,
    )
    from app.services.batch_service import run_batch_job

    async with AsyncSessionLocal() as db:
        schedule = await db.get(ScheduledBatch, schedule_id)
        if not schedule:
            logger.error("scheduled_batch.not_found", schedule_id=schedule_id)
            return {"error": "schedule not found"}

        file_paths: list[tuple[str, str]] = []

        if schedule.drive_folder_id:
            try:
                from app.services.drive_service import (
                    download_file_to_storage,
                    list_folder_files,
                )

                files = await list_folder_files(schedule.drive_folder_id)
                for f in files:
                    key = await download_file_to_storage(
                        f["id"], f["name"], schedule.user_id
                    )
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

    from app.db.database import AsyncSessionLocal
    from app.models.models import JobStatus, OCRJob
    from app.services.chunking_service import chunk_document
    from app.services.embedding_service import embed_documents, vec_to_pg_str
    from sqlalchemy import text as _text

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
        text = job.result_text
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
    embeddings = await embed_documents(texts_to_embed)

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
                # CAST(:vec AS vector), not :vec::vector — SQLAlchemy's text()
                # bind-parameter parser doesn't recognize a name immediately
                # followed by a Postgres "::" cast, so :vec::vector rendered
                # as literal unbound text and Postgres rejected it with a
                # syntax error (reproduced live: every ingest ran, embedded,
                # and then failed on this exact line).
                _text("""
                    INSERT INTO document_chunks
                        (id, job_id, user_id, chunk_index, content, token_count,
                         embedding, ts_vector, chunk_metadata, created_at)
                    VALUES
                        (:id, :job_id, :user_id, :chunk_index, :content, :token_count,
                         CAST(:vec AS vector),
                         to_tsvector('english', :content),
                         :metadata, NOW())
                """),
                {
                    "id": str(_uuid.uuid4()),
                    "job_id": job_id,
                    "user_id": user_id,
                    "chunk_index": chunk["chunk_index"],
                    "content": chunk["content"],
                    "token_count": chunk["token_count"],
                    "vec": vec_str,
                    "metadata": _json.dumps(chunk.get("metadata", {})),
                },
            )

        await db.commit()

    log.info("ingest.complete", chunks_stored=len(chunks))
    return {"job_id": job_id, "chunks": len(chunks)}


# Task: Webhook retry with exponential backoff
@celery_app.task(
    name="app.worker.tasks.retry_webhook_delivery",
    bind=True,
    queue="webhooks",
    max_retries=0,  # self-managed retry logic — no Celery auto-retry
    acks_late=True,
)
def retry_webhook_delivery(
    self,
    webhook_id: str,
    event_str: str,
    payload_json: str,
    attempt: int,
) -> dict:
    """
    Deliver a single webhook attempt and schedule the next retry on failure.

    Args:
        webhook_id   — Webhook.id to deliver to
        event_str    — event name string (e.g. "job.completed")
        payload_json — full JSON-encoded payload (includes event + timestamp + data)
        attempt      — 1-based attempt number (2 = first Celery-managed retry)

    Backoff schedule (from webhook_service.BACKOFF_DELAYS):
        Attempt 2 → 30 s
        Attempt 3 → 5 min
        Attempt 4 → 30 min
        Attempt 5 → 2 hours (final — permanently_failed if this fails)

    Each attempt is recorded in webhook_deliveries (append-only audit trail).
    """
    return _run_async(_do_webhook_retry(webhook_id, event_str, payload_json, attempt))


async def _do_webhook_retry(
    webhook_id: str,
    event_str: str,
    payload_json: str,
    attempt: int,
) -> dict:
    import hashlib as _hashlib
    import hmac as _hmac
    import json as _json

    import httpx
    from app.db.database import AsyncSessionLocal
    from app.models.models import Webhook, WebhookDelivery
    from app.services.webhook_service import MAX_ATTEMPTS, TIMEOUT_SECS, _schedule_retry

    log = logger.bind(webhook_id=webhook_id[:8], event=event_str, attempt=attempt)

    async with AsyncSessionLocal() as db:
        wh = await db.get(Webhook, webhook_id)

        # Abort if webhook was deleted or deactivated since scheduling
        if not wh or not wh.is_active:
            log.info("webhook.retry_skipped", reason="not_found_or_inactive")
            return {"skipped": True, "reason": "inactive"}

        payload_bytes = payload_json.encode()

        # Build HMAC signature if webhook has a secret
        headers = {
            "Content-Type": "application/json",
            "X-TextLens-Event": event_str,
            "User-Agent": "TextLens-Webhook/2.0",
            "X-Retry-Attempt": str(attempt),
        }
        if wh.secret:
            digest = _hmac.new(
                wh.secret.encode(), payload_bytes, _hashlib.sha256
            ).hexdigest()
            headers["X-TextLens-Signature"] = f"sha256={digest}"

        # Attempt HTTP delivery
        status_code: int | None = None
        error_msg: str | None = None
        success = False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    wh.target_url,
                    content=payload_bytes,
                    headers=headers,
                    timeout=TIMEOUT_SECS,
                )
            status_code = resp.status_code
            success = 200 <= status_code < 300
        except Exception as exc:
            error_msg = str(exc)

        if success:
            log.info("webhook.retry_delivered", status=status_code)
        else:
            log.warning("webhook.retry_failed", status=status_code, error=error_msg)

        # Persist delivery record
        try:
            payload_dict = _json.loads(payload_json)
        except Exception:
            payload_dict = {"raw": payload_json}

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
        wh.last_triggered_at = __import__("datetime").datetime.utcnow()
        wh.total_deliveries = (wh.total_deliveries or 0) + 1
        db.add(wh)
        await db.commit()

    # Schedule next retry if still failing
    if not success:
        next_attempt = attempt + 1
        if next_attempt <= MAX_ATTEMPTS:
            _schedule_retry(webhook_id, event_str, payload_json, next_attempt)
        else:
            logger.warning(
                "webhook.permanently_failed",
                webhook_id=webhook_id[:8],
                event=event_str,
                total_attempts=attempt,
            )

    return {
        "webhook_id": webhook_id,
        "event": event_str,
        "attempt": attempt,
        "success": success,
        "status": status_code,
    }
