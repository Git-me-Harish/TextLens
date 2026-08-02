import uuid
import logging

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.database import engine
from app.db.redis import close_redis
from app.services import storage_service

# Route imports 
from app.api.routes import auth, jobs, users, agents, export
from app.api.routes.sse import router as sse_router
from app.api.routes.batch import router as batch_router
from app.api.routes.apikeys import router as apikeys_router
from app.api.routes.corrections import router as corrections_router
from app.api.routes.chat import router as chat_router
from app.api.routes.drive import router as drive_router
from app.api.routes.schedules import router as schedules_router

# Logging must be the first thing configured 
setup_logging()
logger = structlog.get_logger(__name__)


# Request-ID middleware 
class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Reads X-Request-ID header (or generates a UUID) and:
      1. Binds it to the structlog context for the duration of the request.
      2. Echoes it back in the response header.
    Every log line emitted during the request will carry request_id automatically.
    """
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# App lifespan 
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
      - MinIO bucket created (idempotent — safe on every boot)
      - No create_all — schema is owned by Alembic migrations
        Run: alembic upgrade head  (or see docker-compose command)

    Shutdown:
      - Close Redis connection pool
      - Dispose SQLAlchemy engine
    """
    logger.info("textlens.startup", environment=settings.ENVIRONMENT)

    import os
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)  # temp dir for OCR workers

    await storage_service.ensure_bucket()

    yield

    await close_redis()
    await engine.dispose()
    logger.info("textlens.shutdown")


# Rate limiter 
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)


# FastAPI application 
app = FastAPI(
    title="TextLens API",
    version="2.0.0",
    description="Production-grade document intelligence platform",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Middleware : order matters (outermost first) 
app.add_middleware(RequestIDMiddleware)

# CORS: locked to known origins and an explicit method/header allowlist.
# In development the frontend local URL is always added.
_allowed_origins = [settings.FRONTEND_URL]
if settings.ENVIRONMENT == "development":
    _allowed_origins.append("http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-API-Key",
        "Accept",
    ],
)


# API v1 routers 
V1 = "/api/v1"

app.include_router(auth.router,         prefix=V1)
app.include_router(jobs.router,         prefix=V1)
app.include_router(users.router,        prefix=V1)
app.include_router(agents.router,       prefix=V1)
app.include_router(export.router,       prefix=V1)
app.include_router(batch_router,        prefix=V1)
app.include_router(apikeys_router,      prefix=V1)
app.include_router(corrections_router,  prefix=V1)
app.include_router(chat_router,         prefix=V1)
app.include_router(drive_router,        prefix=V1)
app.include_router(schedules_router,    prefix=V1)
app.include_router(sse_router,          prefix=V1)


# Health endpoints 

@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": "2.0.0", "environment": settings.ENVIRONMENT}


@app.get("/health/deps", tags=["health"])
async def health_deps():
    from app.services.ocr_service import check_dependencies
    deps = check_dependencies()
    deps["ANTHROPIC_API_KEY_set"] = bool(settings.ANTHROPIC_API_KEY)
    deps["GROQ_API_KEY_set"] = bool(settings.GROQ_API_KEY)
    deps["MINIO_BUCKET"] = settings.MINIO_BUCKET
    # Storage check
    try:
        deps["minio_reachable"] = await storage_service.object_exists("__healthcheck__")
        deps["minio_reachable"] = True  # head_object returned without raising
    except Exception:
        deps["minio_reachable"] = False
    all_ok = all(v for k, v in deps.items() if k not in ("OpenCV", "minio_reachable"))
    return {"status": "ok" if all_ok else "degraded", "dependencies": deps}


@app.get("/health/test-ocr", tags=["health"])
async def test_ocr():
    import asyncio
    from app.services.ocr_service import process_job
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text(
            (72, 72),
            "TextLens OCR test\nInvoice #TEST-001\nAmount: $1,234.56",
            fontsize=14,
        )
        tmp_path = "/tmp/ocr_health_test.pdf"
        doc.save(tmp_path)
        doc.close()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, process_job, "pdf_extract", tmp_path, {})
        return {
            "status": "ok" if not result["error"] else "fail",
            "extracted_text": result.get("text", "")[:200],
            "error": result.get("error"),
            "processing_time_ms": result.get("processing_time_ms"),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}