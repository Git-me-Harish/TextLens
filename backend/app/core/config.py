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

    # Rate limiting 
    RATE_LIMIT_PER_MINUTE: int = 30

    # AI providers 
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""

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

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()