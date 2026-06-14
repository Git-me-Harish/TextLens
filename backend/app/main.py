""" TextLens FastAPI application """
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.db.database import engine, Base
from app.db.redis import close_redis
from app.api.routes import auth, jobs, users, agents, export

# Phase 2 routers
from app.api.routes.batch import router as batch_router
from app.api.routes.apikeys import router as apikeys_router
from app.api.routes.corrections import router as corrections_router
from app.api.routes.chat import router as chat_router
from app.api.routes.drive import router as drive_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await close_redis()
    await engine.dispose()


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
app = FastAPI(title="TextLens API", version="3.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router,    prefix="/api")
app.include_router(jobs.router,    prefix="/api")
app.include_router(users.router,   prefix="/api")
app.include_router(agents.router,  prefix="/api")
app.include_router(export.router,  prefix="/api")
app.include_router(batch_router,       prefix="/api")
app.include_router(apikeys_router,     prefix="/api")
app.include_router(corrections_router, prefix="/api")
app.include_router(chat_router,        prefix="/api")
app.include_router(drive_router,       prefix="/api")


# Health endpoints
@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/health/deps")
async def health_deps():
    from app.services.ocr_service import check_dependencies
    deps = check_dependencies()
    deps["ANTHROPIC_API_KEY_set"] = bool(settings.ANTHROPIC_API_KEY)
    deps["GROQ_API_KEY_set"] = bool(settings.GROQ_API_KEY)
    all_ok = all(v for k, v in deps.items() if k != "OpenCV")
    return {"status": "ok" if all_ok else "degraded", "dependencies": deps}


@app.get("/health/test-ocr")
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
