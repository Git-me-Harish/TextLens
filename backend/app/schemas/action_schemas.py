"""
Pydantic v2 schemas for the agentic action layer.

All user-supplied text fields are:
  - Length-bounded
  - HTML-stripped at the validator level
  - No code injection vectors
"""
import re
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# Shared validators
# ─────────────────────────────────────────────────────────────────────────────

def _get_allowed_service_names() -> frozenset[str]:
    from app.services.mcp.registry import MCP_REGISTRY
    return frozenset(MCP_REGISTRY.keys())

# Sourced from registry.py's ACTION_TO_MCP_SERVICES — the single place that maps
# every action_type to its required services (including [] for pure-reasoning
# actions). Previously duplicated here as a hand-maintained frozenset, which
# drifted out of sync with the real catalog (match_resume, the whole education
# domain) and rejected every one of those actions at the API boundary despite
# them existing everywhere else in the system. Derive it instead of copying it.
def _get_allowed_action_types() -> frozenset[str]:
    from app.services.mcp.registry import ACTION_TO_MCP_SERVICES
    return frozenset(ACTION_TO_MCP_SERVICES.keys())


def _strip_html(value: str) -> str:
    """Remove all HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", value).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class StartActionRequest(BaseModel):
    """POST /api/v1/actions/run"""
    agent_run_id: str = Field(
        ...,
        description="ID of the completed AgentRun (document intelligence result) to act upon.",
    )
    action_type: str = Field(
        ...,
        description="One of the catalog action_type values.",
    )
    user_context: str | None = Field(
        None,
        max_length=2000,
        description="Optional free-text context from the user (e.g. preferred pharmacy name).",
    )

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, v: str) -> str:
        v = v.strip().lower()
        allowed = _get_allowed_action_types()
        if v not in allowed:
            raise ValueError(
                f"Unknown action_type '{v}'. "
                f"Allowed: {sorted(allowed)}"
            )
        return v

    @field_validator("user_context")
    @classmethod
    def sanitize_user_context(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _strip_html(v)[:2000]


class ApproveActionRequest(BaseModel):
    """POST /api/v1/actions/{action_run_id}/approve"""
    approval_token: str = Field(
        ...,
        min_length=10,
        max_length=1024,
        description="Short-lived signed JWT returned in the AWAITING_APPROVAL SSE event.",
    )


class RejectActionRequest(BaseModel):
    """POST /api/v1/actions/{action_run_id}/reject"""
    reason: str | None = Field(
        None,
        max_length=500,
        description="Optional reason for rejecting the action.",
    )

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _strip_html(v)[:500]


class SaveCredentialRequest(BaseModel):
    """POST /api/v1/credentials"""
    service_name: str = Field(
        ...,
        description="Service identifier. Must be one of the known MCP service names.",
    )
    credentials: dict[str, Any] = Field(
        ...,
        description="Service-specific credential fields (e.g. {'api_key': '...', 'base_url': '...'}).",
    )

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        v = v.strip().lower()
        allowed = _get_allowed_service_names()
        if v not in allowed:
            raise ValueError(
                f"Unknown service_name '{v}'. "
                f"Allowed: {sorted(allowed)}"
            )
        return v

    @field_validator("credentials")
    @classmethod
    def validate_credentials_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("credentials dict cannot be empty.")
        if len(v) > 20:
            raise ValueError("credentials dict cannot have more than 20 keys.")
        # Sanitize string values
        sanitized = {}
        for key, val in v.items():
            if not isinstance(key, str):
                raise ValueError("credential keys must be strings.")
            if len(key) > 100:
                raise ValueError(f"credential key '{key[:20]}...' exceeds 100 chars.")
            if isinstance(val, str) and len(val) > 4096:
                raise ValueError(f"credential value for '{key}' exceeds 4096 chars.")
            sanitized[key.strip()] = val
        return sanitized


# ─────────────────────────────────────────────────────────────────────────────
# Response schemas
# ─────────────────────────────────────────────────────────────────────────────

class ActionPlanStep(BaseModel):
    step_number: int
    description: str
    tool_name: str | None = None
    requires_external_call: bool = False
    is_reversible: bool = True


class ActionPlan(BaseModel):
    """Structured plan shown to the user before execution (HITL gate)."""
    summary: str
    steps: list[ActionPlanStep]
    estimated_duration_seconds: int
    external_services: list[str] = Field(
        default_factory=list,
        description="Names of external services that will be called.",
    )
    data_to_be_sent: dict[str, Any] = Field(
        default_factory=dict,
        description="Key extracted fields from the document that will be used in the action.",
    )
    risk_level: str = Field(
        default="low",
        description="low|medium|high — used to determine HITL gate behaviour.",
    )


class ActionRunOut(BaseModel):
    """Response for GET /api/v1/actions/{action_run_id}"""
    id: str
    agent_run_id: str | None
    action_type: str
    domain: str
    status: str
    plan: ActionPlan | None = None
    approval_required: bool
    approval_expires_at: datetime | None = None
    action_result: dict[str, Any] | None = None
    error_message: str | None = None
    total_llm_calls: int
    total_tool_calls: int
    total_tokens_used: int
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ActionRunStarted(BaseModel):
    """Response for POST /api/v1/actions/run (202 Accepted)"""
    action_run_id: str
    status: str = "PENDING"
    message: str = "Action run started. Connect to the SSE stream for real-time progress."


class ApprovalGranted(BaseModel):
    action_run_id: str
    status: str = "EXECUTING"
    message: str = "Action approved. Execution will begin shortly."


class ActionRejected(BaseModel):
    action_run_id: str
    status: str = "REJECTED"
    message: str = "Action rejected. No external calls were made."


class AvailableActionOut(BaseModel):
    action_type: str
    label: str
    description: str | None
    requires_credentials: list[str]
    icon: str | None
    is_available: bool = Field(
        description="True if the user has all required credentials connected."
    )
    missing_credentials: list[str] = Field(
        default_factory=list,
        description="Credential service names the user still needs to connect.",
    )

    model_config = {"from_attributes": True}


class AgentRunAvailableActions(BaseModel):
    agent_run_id: str
    domain: str
    available_actions: list[AvailableActionOut]


class CredentialOut(BaseModel):
    service_name: str
    connected: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TraceEventOut(BaseModel):
    id: str
    event_type: str
    tool_name: str | None
    latency_ms: int | None
    success: bool | None
    error_msg: str | None
    ts: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# SSE event payloads
# ─────────────────────────────────────────────────────────────────────────────

class SSEEventType:
    PLAN_READY = "plan_ready"
    APPROVAL_REQUIRED = "approval_required"
    EXECUTING = "executing"
    TOOL_CALLED = "tool_called"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    HEARTBEAT = "heartbeat"


class SSEPlanReadyPayload(BaseModel):
    action_run_id: str
    plan: ActionPlan
    approval_token: str
    approval_expires_at: datetime


class SSEToolCalledPayload(BaseModel):
    action_run_id: str
    tool_name: str
    success: bool
    latency_ms: int


class SSECompletedPayload(BaseModel):
    action_run_id: str
    action_result: dict[str, Any]
    total_tokens_used: int
    total_tool_calls: int


class SSEFailedPayload(BaseModel):
    action_run_id: str
    error_message: str
    recoverable: bool = False
