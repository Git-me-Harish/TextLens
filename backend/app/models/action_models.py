"""
Action layer ORM models.

Tables owned here:
  - AvailableAction  — catalog seeded by migration 004
  - ActionRun        — lifecycle record per user-initiated action
  - AgentTrace       — immutable per-step observability log
  - UserMCPCredential — encrypted external service credentials
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class AvailableAction(Base):
    """Static catalog of actions that can be triggered per domain."""
    __tablename__ = "available_actions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    domain: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON array of service_name strings — ['google_calendar', 'pharmacy_api']
    requires_credentials: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("domain", "action_type", name="uq_available_actions_domain_type"),
    )


class ActionRun(Base):
    """
    Lifecycle record for each user-initiated agentic action.

    State machine:
        PENDING → PLANNING → AWAITING_APPROVAL → EXECUTING → COMPLETED
                           ↘ REJECTED
        PLANNING → FAILED
        EXECUTING → FAILED
        Any → CANCELLED
    """
    __tablename__ = "action_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The document intelligence run that sourced this action
    agent_run_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING",
        comment="PENDING|PLANNING|AWAITING_APPROVAL|EXECUTING|COMPLETED|FAILED|REJECTED|CANCELLED",
    )
    # Structured plan from the planner node, shown to user before execution
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Short-lived signed JWT scoped to this action_run_id
    approval_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    action_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Optional free-text context from the user (max 2000 chars, enforced at schema layer)
    user_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    traces: Mapped[list["AgentTrace"]] = relationship(
        "AgentTrace", back_populates="action_run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_action_runs_user_status", "user_id", "status"),
        Index("ix_action_runs_domain_type", "domain", "action_type"),
    )


class AgentTrace(Base):
    """
    Immutable per-step observability record.
    Never UPDATE or DELETE — append-only audit trail.
    No PII stored here — input/output fields are truncated previews.
    """
    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    action_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("action_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    span_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="llm_call|tool_call|memory_read|hitl_gate|handoff|error",
    )
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Truncated at application layer — never raw user data
    input_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    action_run: Mapped["ActionRun"] = relationship("ActionRun", back_populates="traces")


class UserMCPCredential(Base):
    """
    AES-256-GCM encrypted external service credentials.

    The application-level encryption key is sourced from settings.MCP_ENCRYPTION_KEY
    (env variable) and NEVER stored in the database.
    The `iv` column stores a unique initialization vector per row.
    The `key_version` column enables key rotation without re-encrypting all rows.

    See services/mcp/credential_store.py for encrypt/decrypt implementation.
    """
    __tablename__ = "user_mcp_credentials"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # e.g. 'google_calendar' | 'pharmacy_api' | 'job_board_api' | 'accounting_api' | 'email_api'
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # AES-256-GCM encrypted JSON blob containing the service credentials
    encrypted_credentials: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    # 16-byte GCM initialization vector — unique per row
    iv: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    # Increment when rotating encryption keys
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "service_name", name="uq_user_mcp_credentials_user_service"),
    )
