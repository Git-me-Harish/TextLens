from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, Any
from datetime import datetime
from app.models.models import UserRole, JobType, JobStatus, AgentStatus, AgentDomain


# Auth
class UserRegister(BaseModel):
    email: EmailStr
    full_name: str
    password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
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
    def password_strength(cls, v):
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
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    jobs: list[JobOut]
    total: int
    page: int
    per_page: int


class QuestionRequest(BaseModel):
    question: str
    job_id: str


# Agents
class AgentRunRequest(BaseModel):
    job_id: str                        # OCR job to run agent on
    domain: str                        # finance, healthcare, legal, etc.
    pipeline_type: str                 # invoice_processor, resume_parser, etc.
    user_instructions: Optional[str] = ""


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
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class AgentRunListResponse(BaseModel):
    runs: list[AgentRunOut]
    total: int
    page: int
    per_page: int
