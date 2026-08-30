# Agentic Document Intelligence & Action Platform
## Architecture Design Record (ADR)

**Status**: Accepted  
**Version**: 1.0  
**Base**: TextLens OCR Platform (FastAPI + React/Vite + PostgreSQL + Redis + MinIO)

> **Implementation note (post-build correction):** ADR-001 and ADR-003 below
> record the original decision to build the agent execution engine on
> LangGraph. That is **not what was shipped**. `base_agent.py` implements a
> hand-rolled async ReAct loop (think → act → observe) against the Anthropic
> Messages API directly — there is no LangGraph `StateGraph`, no
> `PostgresSaver` checkpointing, and no `interrupt_before` node. The HITL gate
> is implemented as: `run()` returns early with an `AWAITING_APPROVAL` status
> before entering the loop, the plan and a signed approval token are persisted
> on the `ActionRun` row, and a *separate* Celery task
> (`resume_action_task` → `resume_after_approval()`) later restarts execution
> from scratch using that persisted plan as context. There is no suspended
> coroutine or graph checkpoint being resumed. The `langgraph_thread_id`
> column referenced below was dropped in migration `007` since nothing ever
> wrote to it. This note exists so this document doesn't mislead future
> readers about what's actually running — see `base_agent.py`'s module
> docstring for the accurate architecture description.

---

## 1. System Overview

This document describes the full architecture for evolving the existing TextLens OCR platform into a production-grade **Agentic Document Intelligence & Action Platform** — a system that not only extracts and classifies document content but routes it through domain-specific agents that execute real-world actions via MCP (Model Context Protocol) tool integrations.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    AGENTIC DOCUMENT INTELLIGENCE PLATFORM               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  [React/Vite Frontend]  ──── HTTPS / REST+SSE ────  [FastAPI Backend]  │
│                                                                         │
│  LAYER 1: Document Ingestion (existing — MinIO + OCR)                  │
│       ↓                                                                 │
│  LAYER 2: OCR & Document Intelligence (existing — PyMuPDF/Tesseract)   │
│       ↓                                                                 │
│  LAYER 3: Semantic Understanding (existing — Claude API, domain prompts)│
│       ↓                                                                 │
│  LAYER 4: Domain Classification (existing — auto_classify pipeline)    │
│       ↓                                                                 │
│  LAYER 5: Action Router  ← NEW ─────────────────────────────────────── │
│       ↓                                                                 │
│  LAYER 6: Domain Agent Execution (hand-rolled async ReAct loop) ← NEW │
│       ↓                                                                 │
│  LAYER 7: MCP Tool Layer (real external services) ← NEW ────────────── │
│                                                                         │
│  CROSS-CUTTING: HITL Gates | Observability | Security | Rate Limiting   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Decisions

### ADR-001: Action Execution via LangGraph + MCP — SUPERSEDED, see note above
**Context**: Need to execute domain-specific multi-step workflows from document intelligence output.  
**Original decision**: Use LangGraph (ReAct pattern) as the agent execution engine. Each domain agent is a compiled LangGraph graph with domain-specific tool sets backed by real MCP servers.  
**Why LangGraph over raw tool-calling** (original reasoning):
- Checkpointing via PostgresSaver → survive process failures
- Typed state management → no state drift
- Human-in-the-loop interrupts → built-in `interrupt_before` and approval nodes
- Observable graph execution → every node/edge produces a trace event

**Alternatives considered**: LlamaIndex Workflows (less LangGraph maturity), CrewAI (opinionated, harder to test), raw ReAct loop (too much bespoke plumbing)

**What actually shipped**: the "raw ReAct loop" alternative — a hand-rolled
async loop in `base_agent.py`, no LangGraph dependency at all. Checkpointing
and typed state management were not carried over: state is an in-memory dict
scoped to a single Celery task, and the HITL "resume" is a fresh execution
from a persisted plan, not a graph checkpoint restore. Revisit this ADR if
multi-turn, mid-loop resumption is ever actually needed.

---

### ADR-002: MCP Server Integration Strategy
**Context**: Real external actions require real external APIs (calendars, pharmacies, job boards).  
**Decision**: Each domain action is backed by a real MCP server. We implement a **custom MCP proxy layer** (`MCPRegistry`) that:
1. Maps domain+action_type → MCP server endpoint
2. Handles authentication injection per server (API keys from user settings, not tool parameters)
3. Enforces tool call allowlists per domain agent
4. Wraps every MCP call with circuit breaker + retry logic

---

### ADR-003: Human-in-the-Loop (HITL) Gate Design
**Context**: Actions with real-world consequences (medicine orders, job applications, calendar events) must not execute without user confirmation.  
**Decision**: All "write" actions go through a HITL gate. As shipped: the agent's `run()` method returns `{"status": "AWAITING_APPROVAL", "plan": ...}` before ever entering the ReAct loop; the Celery task persists the plan and a signed approval token on the `ActionRun` row and emits an SSE event to the frontend with a structured `ApprovalRequest`; a separate `resume_action_task` later starts a fresh execution once the user confirms via that signed approval token. (Originally specced as a LangGraph `interrupt_before` node with checkpoint persistence — see the note at the top of this document for why that's not what's running.)  
**Timeout**: 15 minutes — after which the action is auto-rejected (fail closed).

---

### ADR-004: Action Run State Machine
```
PENDING → PLANNING → AWAITING_APPROVAL → EXECUTING → COMPLETED
                                       ↘ REJECTED
          PLANNING → FAILED (plan error)
          EXECUTING → FAILED (tool error, max retries exceeded)
          Any state → CANCELLED (user-initiated)
```

---

### ADR-005: Tenancy & Security
**Context**: Each user's action runs must be fully isolated.  
**Decision**: 
- `action_runs` table has `user_id` FK on every row + RLS policy
- MCP credentials are stored in encrypted `user_mcp_credentials` table (AES-256 at rest)  
- Every agent tool call is scoped to the authenticated user's credentials
- Agent cannot call external services with another user's credentials (enforced at MCPRegistry layer)

---

## 3. Component Architecture

### 3.1 Action Router (`/api/v1/actions`)
Receives `{agent_run_id, action_type, action_params}`, validates the action is permitted for the document's domain, creates an `ActionRun` record, and dispatches to Celery.

### 3.2 Domain Agents
Six domain agents, each a plain `BaseAgent` subclass running the async ReAct loop (not a LangGraph `StateGraph` — see note at the top of this document):

| Agent | Domain | Available Actions | MCP Backends |
|---|---|---|---|
| `HealthcareAgent` | healthcare | `book_appointment`, `order_medicines`, `create_medication_schedule`, `explain_prescription`, `medical_assistant` | calendar MCP, pharmacy MCP |
| `CareerAgent` | hr | `find_jobs`, `apply_to_job`, `optimize_resume`, `generate_interview_prep` | jobs MCP, email MCP |
| `FinanceAgent` | finance | `create_expense_entry`, `validate_invoice`, `generate_report`, `prepare_accounting_data` | accounting MCP, email MCP |
| `LegalAgent` | legal | `summarize_document`, `extract_clauses`, `track_deadlines`, `document_qa` | calendar MCP, email MCP |
| `GovernmentAgent` | government | `summarize_filing`, `extract_obligations`, `flag_risks`, `document_qa` | calendar MCP |
| `EducationAgent` | education | `summarize_document`, `generate_study_material`, `document_qa`, `generate_quiz`, `create_learning_plan` | none — pure reasoning |

### 3.3 MCP Registry
Central registry that maps action types to MCP server definitions. Each server entry includes:
- `base_url` — SSE or HTTP endpoint of the MCP server
- `auth_strategy` — `api_key_header | bearer | none`
- `credential_key` — key to look up in `user_mcp_credentials`
- `allowed_tools` — allowlist of tool names this agent may call
- `timeout_seconds`, `max_retries`

### 3.4 Approval Service
- `POST /api/v1/actions/{action_run_id}/approve` — transitions the run to EXECUTING and dispatches `resume_action_task`, which starts a fresh agent execution using the persisted plan
- `POST /api/v1/actions/{action_run_id}/reject` — cancels the paused thread
- Both endpoints require the user to be the owner of the action run
- Approval token is a short-lived (15min) signed JWT scoped to the action_run_id

### 3.5 Action Observability
Every agent action emits an `AgentTraceEvent` to a `agent_traces` table:
```
trace_id | span_id | parent_span_id | action_run_id | event_type | 
tool_name | input_tokens | output_tokens | latency_ms | success | error_msg | ts
```
This powers the "Action History" UI and internal cost tracking.

---

## 4. Data Model (New Tables)

### `action_runs`
Primary record for each user-initiated action execution.
```sql
id UUID PK
user_id UUID FK users.id (CASCADE DELETE)
agent_run_id UUID FK agent_runs.id  -- the document intelligence that sourced this
action_type VARCHAR(100)            -- e.g. 'order_medicines', 'find_jobs'
domain VARCHAR(50)
status VARCHAR(30)                  -- PENDING|PLANNING|AWAITING_APPROVAL|EXECUTING|COMPLETED|FAILED|REJECTED|CANCELLED
plan JSONB                          -- structured plan produced by planner node
approval_required BOOLEAN DEFAULT TRUE
approval_token VARCHAR(512)         -- signed JWT, null after approval/rejection
approval_expires_at TIMESTAMPTZ
approved_at TIMESTAMPTZ
approved_by_user_id UUID
action_result JSONB                 -- final result from agent
error_message TEXT
total_llm_calls INT DEFAULT 0
total_tool_calls INT DEFAULT 0
total_tokens_used INT DEFAULT 0
created_at TIMESTAMPTZ DEFAULT NOW()
completed_at TIMESTAMPTZ
```
RLS: `USING (user_id = current_setting('app.current_tenant_id')::UUID)`

### `agent_traces`
Immutable audit log of every agent step.
```sql
id UUID PK
action_run_id UUID FK action_runs.id
trace_id VARCHAR(128)
span_id VARCHAR(128)
parent_span_id VARCHAR(128)
event_type VARCHAR(50)              -- llm_call|tool_call|memory_read|handoff|hitl_gate
tool_name VARCHAR(128)
input_preview TEXT                  -- first 500 chars of input (no PII)
output_preview TEXT                 -- first 500 chars of output
input_tokens INT
output_tokens INT
latency_ms INT
success BOOLEAN
error_msg TEXT
ts TIMESTAMPTZ DEFAULT NOW()
```
No UPDATE or DELETE — append-only audit trail.

### `user_mcp_credentials`
Encrypted external service credentials per user.
```sql
id UUID PK
user_id UUID FK users.id (CASCADE DELETE)
service_name VARCHAR(100)           -- 'google_calendar' | 'pharmacy_api' | etc.
encrypted_credentials BYTEA         -- AES-256-GCM encrypted JSON blob
iv BYTEA                            -- initialization vector
key_version INT DEFAULT 1           -- for key rotation
created_at TIMESTAMPTZ DEFAULT NOW()
updated_at TIMESTAMPTZ DEFAULT NOW()
UNIQUE(user_id, service_name)
```

### `available_actions`
Catalog of actions available per domain (drives the frontend action picker).
```sql
id UUID PK
domain VARCHAR(50)
action_type VARCHAR(100)
label VARCHAR(200)
description TEXT
requires_credentials TEXT[]         -- list of service_names user must have connected
icon VARCHAR(50)
is_enabled BOOLEAN DEFAULT TRUE
sort_order INT DEFAULT 0
```

---

## 5. API Contract

### Action Discovery
```
GET /api/v1/actions/catalog
→ { domain: { action_type: { label, description, requires_credentials, icon } } }

GET /api/v1/actions/agent-run/{agent_run_id}/available
→ { available_actions: [...], missing_credentials: { action_type: [service_name] } }
```

### Action Execution
```
POST /api/v1/actions/run
Body: { agent_run_id: UUID, action_type: str, user_context: dict | null }
→ 202 { action_run_id: UUID, status: "PENDING" }

GET /api/v1/actions/{action_run_id}
→ { id, status, plan, action_result, error_message, trace_summary }

SSE /api/v1/actions/{action_run_id}/stream
→ stream of { event: "plan_ready"|"approval_required"|"executing"|"completed"|"failed", data: {...} }
```

### Approval
```
POST /api/v1/actions/{action_run_id}/approve
Body: { approval_token: str }
→ 200 { status: "EXECUTING" }

POST /api/v1/actions/{action_run_id}/reject
Body: { reason: str | null }
→ 200 { status: "REJECTED" }
```

### Credential Management
```
GET  /api/v1/credentials
POST /api/v1/credentials
Body: { service_name: str, credentials: dict }
DELETE /api/v1/credentials/{service_name}
```

---

## 6. Security Architecture

### Input Validation
- All `action_params` validated via Pydantic v2 models before entering agent loop
- `user_context` field limited to 2000 chars, HTML stripped, no code injection
- File paths validated to prevent path traversal

### Secrets Management
- MCP credentials encrypted at rest (AES-256-GCM) with application-level key
- Key stored in environment variable (never in DB) — rotatable via `key_version`
- ANTHROPIC_API_KEY accessed only via `settings` object, never passed as tool param

### Agent Safety
- `MAX_AGENT_ITERATIONS = 15` hard cap on all agents
- `MAX_TOOL_CALLS_PER_ACTION = 30` hard cap
- `ACTION_TIMEOUT_SECONDS = 300` (5 min) — Celery task_time_limit
- All high-risk tool calls require HITL confirmation (see ADR-003)
- Circuit breaker per MCP server (5 failures → 60s open)

### Authorization
- Action runs checked for ownership before every state transition
- Approval endpoint requires matching `user_id` + valid signed approval token
- MCP credential lookup scoped to `user_id` — cross-user credential access impossible by design

---

## 7. Observability Stack

### Structured Logging (structlog — already in place)
Every log line from the agent layer includes:
- `action_run_id`, `trace_id`, `user_id` (never PII)
- `event_type`, `tool_name`, `latency_ms`, `success`

### Metrics (expose via `/metrics` — Prometheus scraping)
- `action_run_total{domain, action_type, status}` counter
- `action_run_duration_seconds{domain, action_type}` histogram
- `tool_call_total{tool_name, success}` counter
- `hitl_approval_rate{action_type}` gauge
- `mcp_circuit_breaker_state{service_name}` gauge

### Agent Traces → `agent_traces` table
Full per-step audit trail queryable via `/api/v1/admin/traces/{action_run_id}`.

---

## 8. Build Phases

### Phase A — Foundation (build now)
1. DB migrations: `action_runs`, `agent_traces`, `user_mcp_credentials`, `available_actions`
2. `MCPRegistry` + circuit breaker
3. `BaseAgent` base class (hand-rolled async ReAct loop + HITL — no LangGraph)
4. All 6 domain agents (Healthcare, Career, Finance, Legal, Government, Education) — wired to real MCP endpoints
5. Action routes (`/api/v1/actions/*`)
6. Credential routes (`/api/v1/credentials`)
7. Approval service
8. Celery tasks for async action execution
9. SSE stream for real-time action progress
10. Frontend: Action picker panel, approval modal, action history page

### Phase B — Production Hardening
1. Per-user token budget enforcement
2. Prometheus metrics endpoint
3. Admin trace viewer
4. MCP server health dashboard
5. Action retry UI

---

## 9. File Structure

```
backend/app/
├── services/
│   ├── actions/
│   │   ├── __init__.py
│   │   ├── base_agent.py          ← Base agent (async ReAct loop + HITL, no LangGraph)
│   │   ├── healthcare_agent.py    ← Healthcare domain agent
│   │   ├── career_agent.py        ← Career/HR domain agent
│   │   ├── finance_agent.py       ← Finance domain agent
│   │   ├── legal_agent.py         ← Legal domain agent
│   │   ├── government_agent.py    ← Government domain agent
│   │   ├── education_agent.py     ← Education domain agent
│   │   └── agent_router.py        ← Domain → agent dispatcher
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── registry.py            ← MCP server registry + circuit breaker
│   │   ├── client.py              ← MCP HTTP/SSE client wrapper
│   │   └── credential_store.py    ← Encrypted credential management
│   ├── action_service.py          ← Orchestrates action run lifecycle
│   └── approval_service.py        ← HITL token generation + verification
├── api/routes/
│   ├── actions/
│   │   ├── __init__.py
│   │   ├── runs.py                ← POST /run, GET /{id}, SSE /{id}/stream
│   │   ├── approvals.py           ← POST /{id}/approve, POST /{id}/reject
│   │   └── catalog.py             ← GET /catalog, GET /agent-run/{id}/available
│   └── credentials.py             ← GET/POST/DELETE /credentials
├── models/
│   └── action_models.py           ← SQLAlchemy ORM for new tables
├── schemas/
│   └── action_schemas.py          ← Pydantic v2 schemas for new APIs
└── worker/
    └── action_tasks.py            ← Celery tasks for agent execution
```
