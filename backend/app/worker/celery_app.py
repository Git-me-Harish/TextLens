"""
Celery application — uses Redis as both broker and result backend.
Beat scheduler handles cron-based scheduled batches.
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "textlens",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,           # re-queue on worker crash
    worker_prefetch_multiplier=1,  # one task at a time per worker
    result_expires=86400,          # results kept 24h
)

# Beat schedule — poll every minute to check due ScheduledBatches
celery_app.conf.beat_schedule = {
    "check-scheduled-batches": {
        "task": "app.worker.tasks.check_and_dispatch_schedules",
        "schedule": 60.0,   # every 60 seconds
    },
}