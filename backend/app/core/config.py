"""
Application settings — all config via environment variables / .env file.

MinIO block:
  MINIO_ENDPOINT    — e.g. http://minio:9000  (Docker service name)
  MINIO_ACCESS_KEY  — MinIO root user
  MINIO_SECRET_KEY  — MinIO root password
  MINIO_BUCKET      — bucket name (created on startup if absent)
  MINIO_PUBLIC_URL  — public-facing URL for presigned URLs
                      (same as MINIO_ENDPOINT when running locally)

Email block (Resend — free tier: 3,000/month, 100/day):
  RESEND_API_KEY    — re_... API key (leave blank to disable notifications)
  FROM_EMAIL        — verified sender address
"""
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    # Database 
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/textlens"

    # Redis / Celery 
    REDIS_URL: str = "redis://localhost:6379/0"

    # Auth 
    SECRET_KEY: str = "change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Google OAuth2
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    GOOGLE_CALENDAR_REDIRECT_URI: str = "http://localhost:8000/api/v1/credentials/google_calendar/callback"

    # CORS 
    FRONTEND_URL: str = "http://localhost:5173"

    # MinIO (S3-compatible) 
    MINIO_ENDPOINT: str = "http://localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "textlens"
    MINIO_PUBLIC_URL: str = "http://localhost:9000"

    # Upload constraints
    MAX_FILE_SIZE_MB: int = 50
    TESSERACT_LANGUAGES: str = "eng+hin"

    # Structured OCR (PaddleOCR / PP-StructureV3) — real table/formula/
    # handwriting-aware extraction, layered on top of the always-available
    # Tesseract path (see ocr_service.py). Defaults to OFF: the model
    # cascade needs ~1.7GB RAM at inference time, verified live to crash
    # smaller dev machines when left always-on. The `ocr` queue is always
    # consumed regardless of this flag — Tesseract extraction keeps working
    # out of the box for anyone running this project with zero setup;
    # this only gates the heavier engine on top of it. Flip to true in your
    # own .env (never commit it as true) once you've confirmed your machine
    # has the headroom — Docker Desktop's WSL2 VM needs several GB free.
    ENABLE_STRUCTURED_OCR: bool = False

    # Rate limiting 
    RATE_LIMIT_PER_MINUTE: int = 30

    # AI providers 
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Agentic action layer
    AGENT_MODEL: str = Field(
        default="claude-sonnet-4-20250514",
        description="Anthropic model ID used for all domain agents.",
    )
    AGENT_MAX_ITERATIONS: int = Field(default=15, ge=1, le=20)
    AGENT_MAX_TOOL_CALLS: int = Field(default=30, ge=1, le=50)
    MCP_ENCRYPTION_KEY: str = Field(
        default="",
        description="AES-256-GCM key (64 hex chars) for MCP credential encryption.",
    )
    MCP_KEY_VERSION: int = Field(default=1, ge=1)
    APPROVAL_TOKEN_TTL_MINUTES: int = Field(default=15, ge=1, le=60)
    INTERNAL_MCP_SHARED_SECRET: str = Field(
        default="",
        description=(
            "Sent as X-Internal-MCP-Secret on every outbound call to a self-hosted "
            "MCP proxy (see registry.py's call_mcp_tool). Required by any proxy that "
            "has no other way to gate access — e.g. mcp_email.py, which authenticates "
            "to Resend with our own platform API key rather than a per-user token, so "
            "without this check it would be an open relay for anyone who finds the URL. "
            "Calendar's proxy checks it too, as defense in depth alongside its "
            "per-user Google bearer token."
        ),
    )
    ACTION_CELERY_QUEUE: str = Field(default="actions")
    ACTION_TASK_TIME_LIMIT: int = Field(default=330)
    GOOGLE_CALENDAR_MCP_URL: str = "http://localhost:8000/mcp/google-calendar"
    EMAIL_MCP_URL: str = "http://localhost:8000/mcp/email"
    PHARMACY_MCP_URL: str = "http://localhost:8000/mcp/pharmacy"
    JOB_BOARD_MCP_URL: str = "http://localhost:8000/mcp/job-board"
    ACCOUNTING_MCP_URL: str = "http://localhost:8000/mcp/accounting"

    # Voyage AI embeddings (free tier: 200M tokens/month) -> voyage-3     → 1024 dims, best general quality (default)
    VOYAGE_API_KEY:   str = ""
    VOYAGE_MODEL:     str = "voyage-3"
    EMBEDDING_DIM:    int = 1024          # voyage-3 output dims

    # Email : Resend API
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "TextLens <onboarding@resend.dev>"

    # Environment 
    ENVIRONMENT: str = "development"   # development | production

    UPLOAD_DIR: str = "/tmp/textlens_uploads"

    @field_validator("MCP_ENCRYPTION_KEY")
    @classmethod
    def validate_encryption_key(cls, v: str) -> str:
        if v and len(v) != 64:
            raise ValueError(
                "MCP_ENCRYPTION_KEY must be exactly 64 hex characters (32 bytes). "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
