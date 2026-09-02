"""
Pydantic schemas for TextLens API.
Phase 2 additions: BatchJob, APIKey, Webhook, FieldCorrection, AuditLog.
"""
import re
from pydantic import BaseModel, EmailStr, Field, field_validator, HttpUrl
from typing import Optional, Any, List
from datetime import datetime
from app.models.models import UserRole, JobType, JobStatus, AgentStatus, AgentDomain, BatchStatus


# action_schemas.py's stated policy for user-supplied free text is
# length-bounded + HTML-stripped + no injection vectors. user_instructions
# (AgentRunRequest, BatchJobCreate below) is the same kind of field — a
# small local copy rather than importing action_schemas.py's private
# `_strip_html`, since that module is scoped to the agentic action layer and
# this one to core OCR/agent CRUD; they're kept independent on purpose.
def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


_INSTRUCTIONS_MAX_LEN = 2000


# Auth
class UserRegister(BaseModel):
    email: EmailStr
    full_name: str
    password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


# User
class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    avatar_url: Optional[str]
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None


# Jobs
class JobOut(BaseModel):
    id: str
    job_type: JobType
    status: JobStatus
    original_filename: str
    result_text: Optional[str]
    result_file_path: Optional[str]
    error_message: Optional[str]
    page_count: Optional[int]
    processing_time_ms: Optional[int]
    ocr_confidence: Optional[float] = None
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    jobs: List[JobOut]
    total: int
    page: int
    per_page: int


class QuestionRequest(BaseModel):
    question: str
    job_id: str


# Agents
class AgentRunRequest(BaseModel):
    job_id: str
    domain: str
    pipeline_type: str
    # Genuinely shapes the model's output — appended to the prompt as
    # "Additional instructions from the user" (agent_service.py::run_agent).
    # Previously unbounded and unsanitized, unlike every other free-text
    # field in this app; capped and HTML-stripped for the same reasons
    # action_schemas.py documents for user_context.
    user_instructions: Optional[str] = Field(default="", max_length=_INSTRUCTIONS_MAX_LEN)

    @field_validator("user_instructions")
    @classmethod
    def sanitize_user_instructions(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        return _strip_html(v)[:_INSTRUCTIONS_MAX_LEN]


class AgentRunOut(BaseModel):
    id: str
    domain: AgentDomain
    pipeline_type: str
    status: AgentStatus
    structured_result: Optional[Any]
    summary: Optional[str]
    confidence_score: Optional[int]
    error_message: Optional[str]
    processing_time_ms: Optional[int]
    original_filename: Optional[str]
    user_instructions: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class AgentRunListResponse(BaseModel):
    runs: List[AgentRunOut]
    total: int
    page: int
    per_page: int


# Corrections
class FieldCorrectionCreate(BaseModel):
    """Submit one or more field corrections for an agent result."""
    corrections: List[dict]   # [{field_path: str, corrected_value: str}]


class FieldCorrectionOut(BaseModel):
    id: str
    agent_run_id: str
    field_path: str
    original_value: Optional[str]
    corrected_value: str
    created_at: datetime

    class Config:
        from_attributes = True


# Batch
class BatchJobCreate(BaseModel):
    name: Optional[str] = "Batch Job"
    domain: str
    pipeline_type: str
    user_instructions: Optional[str] = ""


class BatchItemOut(BaseModel):
    id: str
    original_filename: str
    status: BatchStatus
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class BatchJobOut(BaseModel):
    id: str
    name: str
    domain: AgentDomain
    pipeline_type: str
    status: BatchStatus
    total_files: int
    completed_files: int
    failed_files: int
    user_instructions: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    items: List[BatchItemOut] = []

    class Config:
        from_attributes = True


class BatchJobListResponse(BaseModel):
    batches: List[BatchJobOut]
    total: int
    page: int
    per_page: int


# API keys
class APIKeyCreate(BaseModel):
    name: str
    monthly_limit: Optional[int] = None
    expires_at: Optional[datetime] = None


class APIKeyOut(BaseModel):
    """Returned after creation — plain_key only visible once."""
    id: str
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: Optional[datetime]
    total_requests: int
    monthly_limit: Optional[int]
    created_at: datetime
    expires_at: Optional[datetime]
    # only set on initial creation response
    plain_key: Optional[str] = None

    class Config:
        from_attributes = True


class APIKeyListResponse(BaseModel):
    keys: List[APIKeyOut]
    total: int


# Webhooks
class WebhookCreate(BaseModel):
    name: str
    target_url: str
    events: List[str]           # list of WebhookEvent values
    secret: Optional[str] = None


class WebhookOut(BaseModel):
    id: str
    name: str
    target_url: str
    events: List[str]
    is_active: bool
    last_triggered_at: Optional[datetime]
    total_deliveries: int
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookDeliveryOut(BaseModel):
    id: str
    event: str
    status_code: Optional[int]
    success: bool
    error_message: Optional[str]
    attempt: int
    created_at: datetime

    class Config:
        from_attributes = True


# Audit
class AuditLogOut(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: str
    extra_data: Optional[Any]
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    logs: List[AuditLogOut]
    total: int
    page: int
    per_page: int


class NotificationOut(BaseModel):
    id: str
    type: str
    status: str
    title: str
    message: Optional[str]
    link: Optional[str]
    entity_type: Optional[str]
    entity_id: Optional[str]
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    notifications: List[NotificationOut]
    total: int
    unread_count: int
