"""
TextLens ORM models — Phase 2 additions:
  - BatchJob + BatchItem for bulk processing
  - APIKey for enterprise REST access
  - Webhook for async notifications
  - AuditLog for immutable processing trail
  - FieldCorrection for human-in-the-loop feedback loop
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    String, Boolean, DateTime, Text, Integer,
    ForeignKey, Enum as SAEnum, JSON, BigInteger, Float,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector
import enum
from app.db.database import Base

# Voyage AI voyage-3 output dimensionality.
# Change here + run migration 003 if you switch embedding models.
VOYAGE_EMBEDDING_DIM = 1024


# enums 
class UserRole(str, enum.Enum):
    admin = "admin"
    user  = "user"


class JobStatus(str, enum.Enum):
    pending    = "pending"
    processing = "processing"
    completed  = "completed"
    failed     = "failed"


class JobType(str, enum.Enum):
    ocr_image     = "ocr_image"
    pdf_extract   = "pdf_extract"
    pdf_summarize = "pdf_summarize"
    pdf_qa        = "pdf_qa"
    pdf_to_word   = "pdf_to_word"
    image_to_pdf  = "image_to_pdf"
    # Document Studio types — DB enum extended in migration 004, but this
    # Python class was never updated to match, so studio.py's JobType.pdf_merge
    # / JobType.images_to_pdf raised AttributeError and jobs.py's
    # _validate_job_type() rejected pdf_to_markdown/pdf_compress with a 400
    # before they ever reached the worker. Adding the missing members here.
    pdf_to_markdown = "pdf_to_markdown"
    pdf_merge       = "pdf_merge"
    pdf_split       = "pdf_split"
    pdf_compress    = "pdf_compress"
    images_to_pdf   = "images_to_pdf"
    pdf_edit        = "pdf_edit"


class AgentStatus(str, enum.Enum):
    pending   = "pending"
    running   = "running"
    completed = "completed"
    failed    = "failed"


class AgentDomain(str, enum.Enum):
    finance    = "finance"
    healthcare = "healthcare"
    legal      = "legal"
    logistics  = "logistics"
    hr         = "hr"
    education  = "education"
    government = "government"
    general    = "general"


class BatchStatus(str, enum.Enum):
    pending    = "pending"
    processing = "processing"
    completed  = "completed"
    failed     = "failed"
    partial    = "partial"           # some items failed, some succeeded


class WebhookEvent(str, enum.Enum):
    job_completed     = "job.completed"
    job_failed        = "job.failed"
    agent_completed   = "agent.completed"
    batch_completed   = "batch.completed"


# core entities 
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    jobs: Mapped[list["OCRJob"]] = relationship("OCRJob", back_populates="user", cascade="all, delete-orphan")
    agent_runs: Mapped[list["AgentRun"]] = relationship("AgentRun", back_populates="user", cascade="all, delete-orphan")
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship("PasswordResetToken", back_populates="user")
    batch_jobs: Mapped[list["BatchJob"]] = relationship("BatchJob", back_populates="user", cascade="all, delete-orphan")
    api_keys: Mapped[list["APIKey"]] = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    webhooks: Mapped[list["Webhook"]] = relationship("Webhook", back_populates="user", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="user")
    notifications: Mapped[list["Notification"]] = relationship("Notification", back_populates="user", cascade="all, delete-orphan")


class OCRJob(Base):
    __tablename__ = "ocr_jobs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    job_type: Mapped[JobType] = mapped_column(SAEnum(JobType))
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), default=JobStatus.pending)
    original_filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Real Tesseract engine confidence (mean per-word, 0-100) — see
    # ocr_service.py's _ocr_with_confidence. Null for jobs that never ran
    # OCR (native-text PDFs, non-OCR conversions).
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # SHA-256 hex
    # batch linkage — null for standalone jobs
    batch_item_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("batch_items.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="jobs")
    agent_runs: Mapped[list["AgentRun"]] = relationship("AgentRun", back_populates="ocr_job")
    batch_item: Mapped["BatchItem | None"] = relationship("BatchItem", back_populates="ocr_job")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    ocr_job_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("ocr_jobs.id", ondelete="SET NULL"), nullable=True)
    domain: Mapped[AgentDomain] = mapped_column(SAEnum(AgentDomain))
    pipeline_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[AgentStatus] = mapped_column(SAEnum(AgentStatus), default=AgentStatus.pending)
    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 0-100
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # API key used — null if from UI
    api_key_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="agent_runs")
    ocr_job: Mapped["OCRJob | None"] = relationship("OCRJob", back_populates="agent_runs")
    corrections: Mapped[list["FieldCorrection"]] = relationship("FieldCorrection", back_populates="agent_run", cascade="all, delete-orphan")
    api_key: Mapped["APIKey | None"] = relationship("APIKey", back_populates="agent_runs")


# Batch 
class BatchJob(Base):
    """One batch = multiple files processed with the same pipeline."""
    __tablename__ = "batch_jobs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255), default="Batch Job")
    domain: Mapped[AgentDomain] = mapped_column(SAEnum(AgentDomain))
    pipeline_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[BatchStatus] = mapped_column(SAEnum(BatchStatus), default=BatchStatus.pending)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    completed_files: Mapped[int] = mapped_column(Integer, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    user_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="batch_jobs")
    items: Mapped[list["BatchItem"]] = relationship("BatchItem", back_populates="batch_job", cascade="all, delete-orphan")


class BatchItem(Base):
    """Single file within a BatchJob — tracks per-file status."""
    __tablename__ = "batch_items"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("batch_jobs.id", ondelete="CASCADE"))
    original_filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(512))
    status: Mapped[BatchStatus] = mapped_column(SAEnum(BatchStatus), default=BatchStatus.pending)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    batch_job: Mapped["BatchJob"] = relationship("BatchJob", back_populates="items")
    ocr_job: Mapped["OCRJob | None"] = relationship("OCRJob", back_populates="batch_item")


# API keys 
class APIKey(Base):
    """
    Enterprise API key — scoped to a user, stores hashed key.
    Plain key is only returned once at creation time.
    """
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))                   # human label e.g. "prod-server"
    key_prefix: Mapped[str] = mapped_column(String(12), index=True)  # first 8 chars for display: "tl_live_"
    key_hash: Mapped[str] = mapped_column(String(255), unique=True)  # bcrypt hash
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_requests: Mapped[int] = mapped_column(BigInteger, default=0)
    monthly_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="api_keys")
    agent_runs: Mapped[list["AgentRun"]] = relationship("AgentRun", back_populates="api_key")


# Webhooks 
class Webhook(Base):
    """
    User-registered webhook endpoint.
    On matching events, backend POST JSON payload to target_url.
    """
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    target_url: Mapped[str] = mapped_column(String(1024))
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)  # HMAC-SHA256 signing secret
    events: Mapped[list] = mapped_column(JSON, default=list)   # list of WebhookEvent values
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_deliveries: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="webhooks")
    deliveries: Mapped[list["WebhookDelivery"]] = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")


class WebhookDelivery(Base):
    """Delivery attempt log — immutable for audit purposes."""
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    webhook_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("webhooks.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    webhook: Mapped["Webhook"] = relationship("Webhook", back_populates="deliveries")


# Audit 
class AuditLog(Base):
    """
    Immutable processing trail.
    Every significant action (upload, agent run, export, correction) records here.
    Never update or delete rows — append-only.
    """
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(64), index=True)     # "job.created", "agent.run", "export.csv" etc.
    entity_type: Mapped[str] = mapped_column(String(64))            # "ocr_job", "agent_run", "batch_job"
    entity_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped["User"] = relationship("User", back_populates="audit_logs")


class Notification(Base):
    """
    User-facing notification feed — distinct from AuditLog (an append-only
    audit trail with no read state, not meant for direct display). Created
    by notification_service.py alongside the existing job_update/agent_update/
    action_update SSE pushes (see worker/tasks.py, worker/action_tasks.py) so
    the same event both pops a live toast and persists into this list for
    later viewing (dashboard panel, bell dropdown).
    """
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32))  # "job" | "agent" | "action"
    status: Mapped[str] = mapped_column(String(16))  # "completed" | "failed" | "awaiting_approval"
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Frontend route to navigate to on click — e.g. "/history", "/agent-history", "/actions/history"
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "ocr_job" | "agent_run" | "action_run"
    entity_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped["User"] = relationship("User", back_populates="notifications")


# Corrections 
class FieldCorrection(Base):
    """
    Human correction on an agent result field.
    Used for feedback loop — corrections feed back into prompt improvement.
    """
    __tablename__ = "field_corrections"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("agent_runs.id", ondelete="CASCADE"))
    field_path: Mapped[str] = mapped_column(String(255))   # dot-notation: "vendor_name", "line_items[0].amount"
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agent_run: Mapped["AgentRun"] = relationship("AgentRun", back_populates="corrections")



class ScheduledBatch(Base):
    """
    Recurring batch job — runs on a cron schedule via Celery beat.
    drive_folder_id: if set, pulls new files from Google Drive folder each run.
    """
    __tablename__ = "scheduled_batches"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    cron_expr: Mapped[str] = mapped_column(String(64))      # e.g. "0 9 * * 1" = Mon 9am
    domain: Mapped[str] = mapped_column(String(64))
    pipeline_type: Mapped[str] = mapped_column(String(100))
    drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")


class ChatSession(Base):
    """
    Persistent PDF chat session.
    messages: JSONB array of {role, content}.
    suggested_questions: JSONB — Groq-generated starters for this doc.
    """
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("ocr_jobs.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512), default="Chat session")
    messages: Mapped[list] = mapped_column(JSON, default=list)
    suggested_questions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User")
    job: Mapped["OCRJob"] = relationship("OCRJob")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="password_reset_tokens")

class DocumentChunk(Base):
    """
    One semantic chunk of an OCR-extracted document, with its Voyage AI
    dense embedding and PostgreSQL tsvector for BM25 search.

    Lifecycle:
      - Created by the `ingest_document` Celery task after OCR completes.
      - Deleted automatically (CASCADE) when the parent OCRJob is deleted.
      - Re-created on retry ingestion (old rows deleted, new rows inserted).

    Indexes (defined in migration 002):
      HNSW  on embedding   — ANN cosine search
      GIN   on ts_vector   — BM25 keyword search
      B-tree on job_id     — always present in WHERE clause
    """
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("ocr_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Dense vector — populated by embedding_service.embed_documents()
    # Not using Mapped[list[float]] because pgvector's Vector type needs
    # explicit SQLAlchemy type annotation bypass.
    embedding = mapped_column(Vector(VOYAGE_EMBEDDING_DIM), nullable=True)

    # Sparse vector — populated via to_tsvector() in the ingest task.
    # Used by BM25 full-text search with PostgreSQL's @@ operator.
    ts_vector = mapped_column(TSVECTOR, nullable=True)

    # Optional structured metadata: page hint, section title, etc.
    chunk_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships (read-only helpers — no back_populates needed)
    job: Mapped["OCRJob"] = relationship("OCRJob")