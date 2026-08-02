"""
Structured logging — structlog + stdlib integration.

Dev  → colored human-readable console output
Prod → JSON lines (stdout) parseable by Datadog/GCP/CloudWatch

Usage anywhere in the app:
    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("job.started", job_id=job_id, user_id=user_id)

Request-scoped context (request_id, user_id) is bound per-request via
RequestContextMiddleware in main.py and automatically included in every
log line emitted within that request.
"""
import logging
import sys

import structlog

from app.core.config import settings


def setup_logging() -> None:
    """Configure structlog + stdlib root logger. Call once at startup."""

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.ENVIRONMENT == "development":
        # Human-readable coloured output for local dev
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # Machine-readable JSON for production log aggregation
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(
        logging.DEBUG if settings.ENVIRONMENT == "development" else logging.INFO
    )

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)