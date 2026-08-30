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

from app.core.config import settings
from celery import Celery

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
        "app.worker.tasks.process_scheduled_batch": {"queue": "default"},
        "action_tasks.execute_action": {"queue": settings.ACTION_CELERY_QUEUE},
        "action_tasks.resume_action": {"queue": settings.ACTION_CELERY_QUEUE},
    },
)

# Beat schedule — check for due ScheduledBatches every 60 seconds
celery_app.conf.beat_schedule = {
    "check-scheduled-batches": {
        "task": "app.worker.tasks.check_and_dispatch_schedules",
        "schedule": 60.0,
    },
}
