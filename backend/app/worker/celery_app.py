"""
Celery application — Redis as broker + result backend.

Queues
──────
  default   — general tasks (schedule dispatch)
  ocr       — OCR processing tasks (CPU-bound, isolated concurrency)
  agents    — Claude API agent runs (I/O-bound, rate-limited)
  ingest    — RAG ingestion tasks (Voyage AI embed + pgvector writes)
  webhooks  — webhook retry delivery (network I/O, exponential backoff)

Routing
───────
  All Celery workers consume all queues by default (see docker-compose).
  In production, scale independently:
    celery -A app.worker.celery_app worker -Q ocr      --concurrency=2
    celery -A app.worker.celery_app worker -Q agents   --concurrency=8
    celery -A app.worker.celery_app worker -Q ingest   --concurrency=4
    celery -A app.worker.celery_app worker -Q webhooks --concurrency=8
"""

import structlog
from app.core.config import settings
from celery import Celery
from celery.signals import worker_ready

logger = structlog.get_logger(__name__)

celery_app = Celery(
    "textlens",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks", "app.worker.action_tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Time
    timezone="UTC",
    enable_utc=True,
    # Reliability
    task_track_started=True,
    task_acks_late=True,  # re-queue if worker crashes mid-task
    worker_prefetch_multiplier=1,  # one task per worker slot — no hoarding
    task_reject_on_worker_lost=True,
    # Results
    result_expires=86_400,  # keep results 24 h
    # Queue routing
    task_default_queue="default",
    task_routes={
        "app.worker.tasks.process_ocr_job": {"queue": "ocr"},
        "app.worker.tasks.process_agent_run": {"queue": "agents"},
        "app.worker.tasks.ingest_document": {"queue": "ingest"},
        "app.worker.tasks.retry_webhook_delivery": {"queue": "webhooks"},
        "app.worker.tasks.check_and_dispatch_schedules": {"queue": "default"},
        "app.worker.tasks.purge_expired_trash": {"queue": "default"},
        "app.worker.tasks.process_scheduled_batch": {"queue": "default"},
        "action_tasks.execute_action": {"queue": settings.ACTION_CELERY_QUEUE},
        "action_tasks.resume_action": {"queue": settings.ACTION_CELERY_QUEUE},
    },
)

@worker_ready.connect
def _warm_ocr_imports(**_kwargs) -> None:
    """
    Pre-import the OCR stack at worker startup instead of inside the first task.

    _run_ocr_job imports pytesseract/fitz lazily (function-level, to keep
    module import cheap and sidestep circularity). The cost doesn't disappear
    though — it lands on whichever job happens to be first, and it is not
    small: pytesseract pulls in pandas, and on a cold filesystem cache the
    whole chain measured ~27s on a real run, against 312ms of actual OCR for
    that same document. The user watched a one-page PDF take 33s and
    reasonably concluded extraction was slow, when almost all of it was
    one-off import cost.

    Paying it here moves the wait into worker boot, where nobody is waiting on
    it, and every job — including the first — sees the warm path.

    Deliberately best-effort: a failure here must never stop the worker from
    starting, since the lazy imports inside the task remain the real
    guarantee. This only front-loads them.
    """
    try:
        import time
        started = time.monotonic()
        from app.services.ocr_service import process_job  # noqa: F401
        logger.info("worker.ocr_imports_warmed", seconds=round(time.monotonic() - started, 2))
    except Exception as exc:
        logger.warning("worker.ocr_warmup_failed", error=str(exc))


# Beat schedule — check for due ScheduledBatches every 60 seconds
celery_app.conf.beat_schedule = {
    "check-scheduled-batches": {
        "task": "app.worker.tasks.check_and_dispatch_schedules",
        "schedule": 60.0,
    },
    # Trash retention sweep — hourly rather than daily so the work stays in
    # small batches and a missed window (worker down at 03:00) self-corrects
    # within the hour instead of waiting another full day.
    "purge-expired-trash": {
        "task": "app.worker.tasks.purge_expired_trash",
        "schedule": 3600.0,
    },
}
