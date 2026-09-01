"""
MCP Registry — the single source of truth mapping domain actions to real MCP servers.

Each entry defines:
  - base_url          : MCP server endpoint
  - auth_strategy     : how credentials are injected into requests
  - credential_key    : the service_name to look up in user_mcp_credentials
  - allowed_tools     : strict allowlist of tool names this agent may call
  - timeout_seconds   : per-call timeout
  - max_retries       : transient failure retries (exponential backoff)

Circuit breaker per server:
  - 5 consecutive failures → circuit OPEN (no calls for 60s)
  - After 60s → circuit HALF-OPEN (one probe call allowed)
  - Probe succeeds → circuit CLOSED again
  - Probe fails → remain OPEN for another 60s

Production MCP server examples wired here use real-world patterns.
Replace base_url values with your actual deployed MCP server URLs.
"""

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.db.redis import get_redis

logger = structlog.get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Normal operation
    OPEN = "OPEN"            # Failing — block all calls
    HALF_OPEN = "HALF_OPEN"  # Probing — allow one call


@dataclass
class CircuitBreaker:
    """
    Per-server circuit breaker, state shared in Redis across every Celery
    worker process (and the FastAPI process, if it ever calls an MCP tool
    directly) — not per-process in-memory.

    The original in-memory version (a dataclass field + asyncio.Lock) only
    ever protected the ONE process it lived in: with multiple Celery
    workers (or even a single worker restarting), each process had its own
    independent failure count and OPEN/CLOSED state, so a service failing
    hard against worker A would keep getting hammered by workers B/C/D —
    exactly the scenario a circuit breaker exists to prevent. wall-clock
    time.time() (not time.monotonic(), which is only comparable within one
    process) is used so the OPEN timestamp is meaningful when read back by
    a different process.

    HALF_OPEN's "exactly one probe call" guarantee is enforced with a
    short-lived Redis SET NX (`probe_lock`) — whichever process's call_allowed()
    wins that race is the only one that proceeds while the circuit is
    recovering; everyone else still sees OPEN until the probe resolves.
    """
    service_name: str
    failure_threshold: int = 5
    recovery_seconds: int = 60

    def _keys(self) -> dict[str, str]:
        prefix = f"mcp_circuit:{self.service_name}"
        return {
            "state": f"{prefix}:state",
            "failures": f"{prefix}:failures",
            "opened_at": f"{prefix}:opened_at",
            "probe_lock": f"{prefix}:probe_lock",
        }

    async def call_allowed(self) -> bool:
        redis = await get_redis()
        keys = self._keys()

        state = await redis.get(keys["state"])
        if state != CircuitState.OPEN.value:
            return True  # CLOSED (or no state recorded yet)

        opened_at = await redis.get(keys["opened_at"])
        elapsed = time.time() - float(opened_at or 0)
        if elapsed < self.recovery_seconds:
            return False

        # Recovery window elapsed — at most one process gets to probe.
        got_probe = await redis.set(keys["probe_lock"], "1", nx=True, ex=max(self.recovery_seconds, 30))
        if got_probe:
            logger.info("circuit_breaker.half_open", service=self.service_name)
        return bool(got_probe)

    async def record_success(self) -> None:
        redis = await get_redis()
        keys = self._keys()
        was_open = await redis.get(keys["state"]) == CircuitState.OPEN.value
        await redis.delete(keys["state"], keys["failures"], keys["opened_at"], keys["probe_lock"])
        if was_open:
            logger.info("circuit_breaker.closed", service=self.service_name)

    async def record_failure(self) -> None:
        redis = await get_redis()
        keys = self._keys()

        failures = await redis.incr(keys["failures"])
        is_probe_failure = bool(await redis.get(keys["probe_lock"]))

        if is_probe_failure or failures >= self.failure_threshold:
            await redis.set(keys["state"], CircuitState.OPEN.value)
            await redis.set(keys["opened_at"], str(time.time()))
            await redis.delete(keys["probe_lock"])
            logger.warning(
                "circuit_breaker.open",
                service=self.service_name,
                failure_count=failures,
            )


# 
# MCP Server definition
# 

@dataclass
class MCPServerDef:
    """Definition of a single MCP server."""
    service_name: str
    base_url: str
    # 'api_key_header' | 'bearer' | 'none'
    auth_strategy: str
    # Maps to the credential_store service_name — None means no auth required
    credential_key: str | None
    # Strict allowlist — agent cannot call tools outside this set
    allowed_tools: frozenset[str]
    timeout_seconds: int = 30
    max_retries: int = 3

    def __post_init__(self):
        self._circuit = CircuitBreaker(service_name=self.service_name)

    @property
    def circuit(self) -> CircuitBreaker:
        return self._circuit


# 
# Registry — all known MCP servers
# 
#
# HOW TO ADD A NEW MCP SERVER:
#  1. Add a MCPServerDef entry below.
#  2. Add the server's tool names to allowed_tools.
#  3. Add the action_type → service_name mapping to ACTION_TO_MCP_SERVICES.
#  4. Add the credential schema to SaveCredentialRequest in action_schemas.py.
#  5. Deploy your MCP server (self-hosted or cloud).
#
# Each MCP server must implement the MCP SSE transport spec:
#  POST {base_url}/call  — JSON body: { tool: str, arguments: dict }
#  Response: { result: any, error: str | null }

MCP_REGISTRY: dict[str, MCPServerDef] = {

    # Google Calendar MCP 
    # Self-hosted or use: https://github.com/googleapis/mcp-server-calendar
    "google_calendar": MCPServerDef(
        service_name="google_calendar",
        base_url=settings.GOOGLE_CALENDAR_MCP_URL,
        auth_strategy="bearer",          # Bearer {user's google_access_token}
        credential_key="google_calendar",
        allowed_tools=frozenset({
            "create_event",
            "list_events",
            "update_event",
            "delete_event",
            "check_availability",
            "find_free_slots",
        }),
        timeout_seconds=20,
        max_retries=2,
    ),

    # Pharmacy / Medicine ordering MCP 
    # Self-hosted (app/api/routes/mcp_pharmacy.py) against this app's own
    # database — no real pharmacy partner account (e.g. 1mg, PharmacyBee)
    # exists to integrate against. credential_key=None means agent_router
    # never asks the user to connect anything for this service, same as
    # email_api.
    "pharmacy_api": MCPServerDef(
        service_name="pharmacy_api",
        base_url=settings.PHARMACY_MCP_URL,
        auth_strategy="none",
        credential_key=None,
        allowed_tools=frozenset({
            "search_medicines",
            "check_medicine_availability",
            "get_medicine_details",
            "create_order",
            "get_order_status",
            "get_order_history",
            "cancel_order",
        }),
        timeout_seconds=30,
        max_retries=3,
    ),

    # Job Board MCP 
    # Self-hosted (app/api/routes/mcp_job_board.py) against this app's own
    # database — LinkedIn/Indeed/Glassdoor partner APIs are gated behind
    # paid/approved access this project doesn't have.
    "job_board_api": MCPServerDef(
        service_name="job_board_api",
        base_url=settings.JOB_BOARD_MCP_URL,
        auth_strategy="none",
        credential_key=None,
        allowed_tools=frozenset({
            "search_jobs",
            "get_job_details",
            "match_resume_to_job",
            "submit_application",
            "get_application_status",
        }),
        timeout_seconds=30,
        max_retries=2,
    ),

    # Accounting MCP 
    # Self-hosted (app/api/routes/mcp_accounting.py) against this app's own
    # database — QuickBooks/Xero/Zoho Books require a registered developer
    # app and OAuth approval this project doesn't have.
    "accounting_api": MCPServerDef(
        service_name="accounting_api",
        base_url=settings.ACCOUNTING_MCP_URL,
        auth_strategy="none",
        credential_key=None,
        allowed_tools=frozenset({
            "create_expense",
            "create_invoice",
            "list_vendors",
            "get_account_list",
            "create_journal_entry",
            "export_report",
            "get_vendor_history",
        }),
        timeout_seconds=25,
        max_retries=3,
    ),

    # Email MCP 
    # Implemented locally (app/api/routes/mcp_email.py) against the platform's
    # own Resend account (settings.RESEND_API_KEY) — not a per-user credential.
    # credential_key=None means agent_router.get_agent() never asks the user
    # to connect anything for this service; auth_strategy="none" means
    # call_mcp_tool() sends no per-call Authorization header (the proxy
    # authenticates to Resend server-side). Access is instead gated by the
    # X-Internal-MCP-Secret header — see INTERNAL_MCP_SHARED_SECRET.
    "email_api": MCPServerDef(
        service_name="email_api",
        base_url=settings.EMAIL_MCP_URL,
        auth_strategy="none",
        credential_key=None,
        allowed_tools=frozenset({
            "send_email",
            "send_email_with_attachment",
            "draft_email",
        }),
        timeout_seconds=15,
        max_retries=2,
    ),
}

# Actions that need no external service (pure Claude reasoning)
NO_CREDENTIAL_SERVICES: frozenset[str] = frozenset({
    "explain_prescription",
    "medical_assistant",
    "create_medication_schedule",
    "match_resume",
    "optimize_resume",
    "generate_interview_prep",
    "validate_invoice",
    "generate_financial_report",
    "summarize_document",
    "extract_key_clauses",
    "document_qa",
    "summarize_filing",
    "extract_obligations",
    "flag_risks",
    "generate_study_material",
    "generate_quiz",
    "create_learning_plan",
    "summarize_shipment",
    "flag_customs_risks",
})

# Which action_type maps to which MCP server(s)
ACTION_TO_MCP_SERVICES: dict[str, list[str]] = {
    # Healthcare
    "book_appointment":          ["google_calendar"],
    "order_medicines":           ["pharmacy_api"],
    # Read-only: pulls the patient's real order history to check the new
    # prescription against, rather than guessing what they're already on.
    "check_medication_interactions": ["pharmacy_api"],
    "create_medication_schedule": [],
    "explain_prescription":      [],
    "medical_assistant":         [],
    # Career
    "find_jobs":                 ["job_board_api"],
    "match_resume":              [],
    "apply_to_job":              ["job_board_api", "email_api"],
    "optimize_resume":           [],
    "generate_interview_prep":   [],
    "schedule_interview":        ["google_calendar"],
    # Finance
    "create_expense_entry":      ["accounting_api"],
    "validate_invoice":          [],
    "generate_financial_report": [],
    "send_payment_reminder":     ["email_api"],
    # Read-only: compares this invoice against the user's own posted
    # ledger so the "unusual" verdict is measured, not asserted.
    "flag_expense_anomalies":    ["accounting_api"],
    # Legal
    "summarize_document":        [],
    "extract_key_clauses":       [],
    "track_obligations":         ["google_calendar"],
    "document_qa":               [],
    # Logistics
    "track_shipment":            ["google_calendar"],
    "notify_consignee":          ["email_api"],
    "record_po_expense":         ["accounting_api"],
    "summarize_shipment":        [],
    "flag_customs_risks":        [],
    # Government
    "summarize_filing":          [],
    "extract_obligations":       [],
    "flag_risks":                [],
    "track_filing_deadlines":    ["google_calendar"],
    # Education
    "generate_study_material":   [],
    "generate_quiz":             [],
    "create_learning_plan":      [],
    "schedule_study_sessions":   ["google_calendar"],
}


def get_server(service_name: str) -> MCPServerDef:
    """Look up a server definition. Raises KeyError for unknown services."""
    if service_name not in MCP_REGISTRY:
        raise KeyError(f"Unknown MCP service: '{service_name}'")
    return MCP_REGISTRY[service_name]


def get_required_services(action_type: str) -> list[str]:
    """Return the list of service_names required for a given action_type."""
    return ACTION_TO_MCP_SERVICES.get(action_type, [])


# 
# MCP HTTP client — the actual call layer
# 

async def call_mcp_tool(
    service_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    user_credentials: dict | None,
) -> Any:
    """
    Call a single MCP tool on the named server.

    - Checks circuit breaker before every call.
    - Injects user credentials via the configured auth strategy.
    - Retries on transient failures (5xx, timeout) with exponential backoff.
    - Validates tool_name is in the server's allowed_tools allowlist.
    - Never logs credential values.

    Returns the raw result value from the MCP server.
    Raises RuntimeError on circuit-open, auth failure, or exhausted retries.
    """
    server = get_server(service_name)

    # Allowlist check — hard gate
    if tool_name not in server.allowed_tools:
        raise ValueError(
            f"Tool '{tool_name}' is not in the allowlist for service '{service_name}'. "
            f"Allowed: {sorted(server.allowed_tools)}"
        )

    # Circuit breaker check
    if not await server.circuit.call_allowed():
        raise RuntimeError(
            f"MCP service '{service_name}' circuit breaker is OPEN. "
            "Service is temporarily unavailable. Try again in 60 seconds."
        )

    # Build auth headers
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.INTERNAL_MCP_SHARED_SECRET:
        headers["X-Internal-MCP-Secret"] = settings.INTERNAL_MCP_SHARED_SECRET
    if server.auth_strategy == "bearer" and user_credentials:
        token = user_credentials.get("access_token") or user_credentials.get("token")
        if not token:
            raise ValueError(f"'{service_name}' requires a bearer token in credentials.")
        headers["Authorization"] = f"Bearer {token}"
    elif server.auth_strategy == "api_key_header" and user_credentials:
        api_key = user_credentials.get("api_key")
        if not api_key:
            raise ValueError(f"'{service_name}' requires 'api_key' in credentials.")
        headers["X-API-Key"] = api_key

    payload = {"tool": tool_name, "arguments": arguments}
    url = f"{server.base_url.rstrip('/')}/call"

    last_error: Exception | None = None
    for attempt in range(server.max_retries + 1):
        if attempt > 0:
            backoff = 2 ** (attempt - 1)  # 1s, 2s, 4s
            logger.info(
                "mcp.retry",
                service=service_name,
                tool=tool_name,
                attempt=attempt,
                backoff_seconds=backoff,
            )
            await asyncio.sleep(backoff)

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=server.timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=payload)

            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 401:
                await server.circuit.record_failure()
                raise ValueError(
                    f"Authentication failed for MCP service '{service_name}'. "
                    "Check your connected credentials."
                )

            if resp.status_code >= 500:
                await server.circuit.record_failure()
                last_error = RuntimeError(
                    f"MCP service '{service_name}' returned {resp.status_code}. Retrying."
                )
                continue

            resp.raise_for_status()
            data = resp.json()

            if data.get("error"):
                # Application-level error from MCP server — not retryable
                await server.circuit.record_failure()
                raise RuntimeError(
                    f"MCP tool '{tool_name}' error: {data['error']}"
                )

            await server.circuit.record_success()
            logger.info(
                "mcp.tool_called",
                service=service_name,
                tool=tool_name,
                latency_ms=latency_ms,
                status="success",
            )
            return data.get("result")

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            await server.circuit.record_failure()
            last_error = exc
            continue

    raise RuntimeError(
        f"MCP tool '{tool_name}' on '{service_name}' failed after "
        f"{server.max_retries + 1} attempts. Last error: {last_error}"
    )
