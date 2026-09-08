# TextLens — Agentic Document Intelligence Platform

TextLens turns a document into two things: **structured data** and **completed work**. Upload an invoice, prescription, contract, or waybill and it's OCR'd, classified into one of 8 industry domains, and extracted into a typed JSON result. From there, an agentic layer can act on it — book the follow-up appointment, file the expense, notify the consignee — through real (and self-hosted) external services, gated behind human approval before anything writes.

It also chats with your documents over a hybrid semantic + keyword search index, batch-processes hundreds of files on a schedule, and recovers anything you delete for 30 days.

---

## Table of Contents

1. [Key Features](#key-features)
2. [Tech Stack](#tech-stack)
3. [Architecture](#architecture)
4. [How It Works](#how-it-works)
5. [Domains — Pipelines & Actions](#domains--pipelines--actions)
6. [Data Model](#data-model)
7. [Background Processing](#background-processing)
8. [Project Structure](#project-structure)
9. [Getting Started](#getting-started)
10. [Environment Variables](#environment-variables)
11. [API Reference](#api-reference)
12. [Security](#security)
13. [Production Checklist](#production-checklist)

---

## Key Features

### Document Intelligence

- OCR (Tesseract + PyMuPDF, with automatic scanned-PDF fallback) and structured PDF extraction
- Document Studio — merge, split, compress, images → PDF, in-browser PDF editing, PDF → Markdown
- Auto-classification — detect the right domain and pipeline from extracted text alone

### Domain Pipelines

8 domains, ~25 specialist extraction pipelines, each returning a typed JSON contract with a confidence score and human-readable summary (see [Domains](#domains--pipelines--actions)).

### Agentic Actions

The layer that turns extraction into completed work.

- 7 domains, 34 actions, routed through a central MCP (Model Context Protocol) service registry
- Real external integration where one exists (Google Calendar OAuth); a working self-hosted stand-in elsewhere (pharmacy, job board, accounting, email) — same contract either way, swappable later
- Every write sits behind a human-in-the-loop approval gate: the agent plans, a signed short-lived token is issued, nothing executes until the user approves
- Cooperative cancellation — stop a queued run before it spends a model call
- Per-service circuit breakers (Redis-backed, shared across workers) so one failing integration can't be hammered by every worker in the pool

### PDF Chat (Hybrid RAG)

Ask questions across your documents. Voyage AI embeddings + pgvector HNSW for semantic search, Postgres BM25 for keyword search, fused with Reciprocal Rank Fusion, cached in Redis. Persisted, resumable sessions.

### Automation & Enterprise

- Batch processing (up to 50 files, ZIP upload) and cron-based Scheduled Batches via Celery Beat, optionally pulling new files from a Google Drive folder each run
- Webhooks (HMAC-signed) for `job.completed`, `agent.completed`, `batch.completed`
- API keys for programmatic access, bcrypt-hashed, usage-tracked

### Trust & Recovery

- Trash — deleting an extraction, pipeline run, action run, chat session, or batch soft-deletes it; recoverable for 30 days, then purged along with its files. API keys, webhooks, and connected credentials are excluded on purpose — those stay hard-deleted
- Human correction feedback loop that improves future extractions, plus an immutable audit log
- MCP credentials encrypted at rest (AES-256-GCM, versioned for rotation); Google's token is refreshed automatically before it expires

### Platform

- JWT auth + Google OAuth2, RBAC (`admin`/`user`), per-IP rate limiting
- Real-time updates over SSE, reconciled against the API so a dropped connection never leaves the UI stuck

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (Python 3.11), async throughout |
| ORM / Migrations | SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 16 + pgvector |
| Cache / Broker / Pub-Sub | Redis 7 |
| Task queue | Celery (worker + beat), 6 queues |
| Object storage | MinIO (S3-compatible) |
| OCR | Tesseract, PyMuPDF, pytesseract, Pillow, OpenCV |
| Document export | python-docx, ReportLab, openpyxl |
| Reasoning LLM | Anthropic Claude — domain pipelines, agentic actions |
| Chat LLM | Groq (`openai/gpt-oss-120b`) — PDF chat answering |
| Embeddings | Voyage AI (`voyage-3`, 1024-dim) |
| Agentic layer | Custom MCP service registry, HITL approval, circuit breakers |
| Frontend | React 18, Vite, React Router v6, TanStack Query |
| Frontend libraries | axios, react-dropzone, react-hook-form, react-hot-toast, lucide-react, date-fns, pdf-lib / pdfjs-dist |
| Auth | JWT (python-jose), bcrypt (passlib), Google OAuth2 (authlib) |
| Infrastructure | Docker, Docker Compose, Nginx |

---

## Architecture

```mermaid
flowchart TB
    Browser["React SPA (Vite)"]

    subgraph API["FastAPI — /api/v1"]
        Core["Core routes<br/>auth · jobs · agents · batch · studio<br/>chat · drive · schedules · trash"]
        Actions["Actions routes<br/>catalog · runs · approvals (SSE)"]
        MCPProxy["MCP proxy routes<br/>self-mounted at /mcp/*"]
    end

    subgraph AgentLayer["Agentic Action Layer"]
        Router["agent_router<br/>(domain, action) → agent + creds"]
        Registry["MCP Registry<br/>allowlist · circuit breaker · retry"]
        Approval["HITL Approval<br/>signed JWT, single-use"]
    end

    subgraph Async["Async Processing"]
        CeleryWorker["Celery Worker<br/>ocr · agents · ingest · actions"]
        CeleryBeat["Celery Beat<br/>schedule dispatch · trash purge"]
    end

    subgraph Data["Data Layer"]
        Postgres[("PostgreSQL 16<br/>+ pgvector")]
        Redis[("Redis<br/>broker · pub/sub · cache · circuit state")]
        MinIO[("MinIO<br/>object storage")]
    end

    subgraph External["External"]
        Claude["Anthropic Claude"]
        Groq["Groq (chat)"]
        Voyage["Voyage AI (embeddings)"]
        GCal["Google Calendar (real OAuth)"]
        SelfHosted["Self-hosted MCP stand-ins<br/>pharmacy · job board · accounting · email"]
    end

    Browser -->|HTTPS + SSE| API
    Core --> Postgres & MinIO
    Core -->|enqueue| CeleryWorker
    Actions --> Router --> Registry --> Approval
    Registry -->|allowlisted calls| MCPProxy
    MCPProxy --> GCal & SelfHosted

    CeleryBeat --> CeleryWorker
    CeleryWorker --> Postgres
    CeleryWorker -->|OCR/pipelines| Claude
    CeleryWorker -->|embed| Voyage
    Core -->|answer| Groq

    API -.->|publish| Redis -.->|SSE stream| Browser
```

---

## How It Works

**1. Extraction → domain pipeline**, real-time over SSE rather than polling — a missed event is reconciled against the API within seconds, never left hanging.

```mermaid
sequenceDiagram
    actor User
    participant FE as React Frontend
    participant API as FastAPI
    participant Worker as Celery Worker
    participant Claude as Anthropic Claude
    participant Redis as Redis (pub/sub)

    User->>FE: Upload invoice.pdf
    FE->>API: POST /jobs/upload
    API-->>FE: 202 (job_id)
    API->>Worker: enqueue process_ocr_job
    Worker->>Worker: PyMuPDF / Tesseract fallback
    Worker->>Redis: publish job_update
    Redis-->>FE: SSE: extraction complete

    FE->>API: POST /agents/run (domain, pipeline)
    API->>Worker: enqueue process_agent_run
    Worker->>Claude: structured extraction prompt
    Claude-->>Worker: typed JSON + confidence
    Worker->>Redis: publish agent_update
    Redis-->>FE: SSE: result ready
    FE->>User: render structured result + actions
```

**2. Agentic action — human-in-the-loop approval.** Every write action is planned first, then held for explicit approval before anything touches a real (or self-hosted) service.

```mermaid
sequenceDiagram
    actor User
    participant FE as React Frontend
    participant API as FastAPI
    participant Router as agent_router
    participant Worker as Celery Worker
    participant MCP as MCP Registry
    participant Svc as External / Self-hosted Service

    User->>FE: Approve "Book follow-up appointment"
    FE->>API: POST /actions/run
    API->>Router: resolve domain agent + inject credentials
    API-->>FE: 202 (action_run_id) — SSE stream opens
    Worker->>Worker: PLANNING — build plan, no side effects yet
    Worker->>API: AWAITING_APPROVAL + signed approval token (SSE)
    FE->>User: show plan, wait for confirmation
    User->>FE: Approve
    FE->>API: POST /actions/{id}/approve (token)
    API->>Worker: resume — EXECUTING
    Worker->>MCP: call_mcp_tool (allowlist + circuit breaker check)
    MCP->>Svc: execute
    Svc-->>MCP: result
    Worker->>API: COMPLETED (SSE)
    FE->>User: show result
```

---

## Domains — Pipelines & Actions

Two independent layers per domain: **pipelines** turn a document into structured data (pure extraction, no side effects); **actions** turn that data into completed work (may write externally, gated by approval).

| Domain | Extraction Pipelines | Agentic Actions (examples) |
|---|---|---|
| **Finance** | Invoice, bank statement, KYC document, cheque, financial report | Create expense entry, validate invoice, flag spending anomalies, send payment reminder |
| **Healthcare** | Medical record, prescription, lab report, insurance claim | Book appointment, order medicines, check medication interactions, create medication schedule |
| **Legal** | Contract, NDA, court document, due diligence | Summarize document, extract key clauses, track obligations, document Q&A |
| **Logistics** | Waybill, purchase order, customs declaration, packing list | Track shipment, notify consignee, record PO expense, flag customs risks |
| **Career (HR)** | Resume/CV parser | Find jobs, match resume, apply to job, generate interview prep, schedule interview |
| **Government** | Tax form, permit/license, regulatory filing (SEC) | Summarize filing, extract obligations, flag risks, track filing deadlines |
| **Education** | Certificate verifier, transcript analyzer | Generate study material, generate quiz, create learning plan, schedule study sessions |

Actions route through a single MCP registry: **Google Calendar** is a real per-user OAuth integration; **pharmacy, job board, and accounting** are working self-hosted stand-ins backed by this app's own database (no partner account exists to integrate against yet — same contract, swappable later); **email** sends through the platform's own account. Every service is allowlisted per tool, retried with backoff, and protected by a Redis-backed circuit breaker shared across every worker.

---

## Data Model

```mermaid
erDiagram
    USER ||--o{ OCR_JOB : owns
    USER ||--o{ AGENT_RUN : owns
    USER ||--o{ ACTION_RUN : owns
    USER ||--o{ BATCH_JOB : owns
    USER ||--o{ CHAT_SESSION : owns
    USER ||--o{ API_KEY : owns
    USER ||--o{ WEBHOOK : owns
    USER ||--o{ USER_MCP_CREDENTIAL : connects

    OCR_JOB ||--o{ AGENT_RUN : feeds
    OCR_JOB ||--o{ DOCUMENT_CHUNK : "embedded into"
    AGENT_RUN ||--o{ ACTION_RUN : "acted on by"
    AGENT_RUN ||--o{ FIELD_CORRECTION : "corrected by"
    ACTION_RUN ||--o{ AGENT_TRACE : logs
    BATCH_JOB ||--o{ BATCH_ITEM : contains
    WEBHOOK ||--o{ WEBHOOK_DELIVERY : logs

    USER {
        uuid id PK
        string email UK
        string role
    }
    OCR_JOB {
        uuid id PK
        uuid user_id FK
        string job_type
        string status
        datetime deleted_at "Trash"
    }
    AGENT_RUN {
        uuid id PK
        uuid ocr_job_id FK
        string domain
        json structured_result
        int confidence_score
        text user_instructions
        datetime deleted_at "Trash"
    }
    ACTION_RUN {
        uuid id PK
        uuid agent_run_id FK
        string action_type
        string status "PLANNING/AWAITING_APPROVAL/..."
        json plan
        string approval_token
        datetime deleted_at "Trash"
    }
    USER_MCP_CREDENTIAL {
        uuid id PK
        string service_name
        bytes encrypted_credentials "AES-256-GCM"
        int key_version
    }
    DOCUMENT_CHUNK {
        uuid id PK
        uuid ocr_job_id FK
        vector embedding "1024-dim, Voyage AI"
        tsvector search_vector "BM25"
    }
    BATCH_JOB {
        uuid id PK
        string status
        text user_instructions
        datetime deleted_at "Trash"
    }
    CHAT_SESSION {
        uuid id PK
        uuid job_id FK
        json messages
        datetime deleted_at "Trash"
    }
    API_KEY {
        uuid id PK
        string key_hash UK
        int monthly_limit
    }
    WEBHOOK {
        uuid id PK
        string target_url
        json events
    }
```

*Not pictured to keep this readable: `Notification`, `ScheduledBatch`, `AvailableAction`, `PasswordResetToken`, and the self-hosted MCP state tables (`PharmacyOrder`, `JobApplication`, `AccountingEntry`).*

---

## Background Processing

```mermaid
flowchart TD
    subgraph OnDemand["Enqueued immediately"]
        Upload["Upload / run pipeline / run action"] --> Enqueue["Celery: ocr / agents / actions queue"]
        Enqueue --> Process["Process"] --> Publish["Publish SSE event (Redis)"]
    end

    subgraph Recurring["Celery Beat"]
        Beat["Beat — every 60s"] --> Dispatch["check_and_dispatch_schedules<br/>due ScheduledBatch → enqueue"]
        BeatHourly["Beat — hourly"] --> Purge["purge_expired_trash<br/>deleted_at < 30 days ago"]
    end

    Dispatch --> DriveWorker["Worker: pull from Drive (if configured)<br/>→ BatchJob → run pipeline per file"]
    Purge --> DeleteRows["Delete DB rows"] --> DeleteFiles["Delete MinIO objects<br/>only after rows committed"]

    subgraph Queues["Queues"]
        Q["default · ocr · agents · ingest · actions · webhooks"]
    end

    Redis[("Redis — broker + pub/sub")] -.-> Beat & Enqueue
```

---

## Project Structure

```
TextLens/
├── docker-compose.yml
├── backend/
│   └── app/
│       ├── main.py                      # router registration, health checks
│       ├── api/
│       │   ├── deps.py
│       │   └── routes/
│       │       ├── auth.py  users.py  jobs.py  agents.py  batch.py
│       │       ├── studio.py            # merge / split / combine / edit
│       │       ├── chat.py  drive.py  schedules.py  notifications.py
│       │       ├── apikeys.py  corrections.py  export.py
│       │       ├── credentials.py       # MCP credential connect/manage
│       │       ├── trash.py             # soft-delete recovery
│       │       ├── sse.py  search.py  analytics.py  admin.py
│       │       ├── actions/             # agentic action layer
│       │       │   ├── catalog.py  runs.py  approvals.py
│       │       └── mcp_*.py             # MCP proxies (calendar/pharmacy/
│       │                                # job board/accounting/email)
│       ├── core/          # config.py, security.py
│       ├── db/            # async engine/session, Redis client
│       ├── models/        # models.py + action_models.py (SQLAlchemy)
│       ├── schemas/       # Pydantic request/response schemas
│       ├── services/
│       │   ├── ocr_service.py  agent_service.py  rag_service.py
│       │   ├── chat_service.py  batch_service.py  trash_service.py
│       │   ├── webhook_service.py  export_service.py  feedback_service.py
│       │   ├── actions/    # per-domain agents (7) + agent_router.py
│       │   └── mcp/        # registry.py, credential_store.py, token_refresh.py
│       └── worker/         # celery_app.py, tasks.py, action_tasks.py
│
└── frontend/
    └── src/
        ├── App.jsx                       # route definitions
        ├── components/
        │   ├── layout/  ui/  actions/  studio/  notifications/
        │   ├── ConfirmDialog.jsx
        │   └── ProtectedRoute.jsx
        ├── lib/
        │   ├── api.js  AuthContext.jsx  AgentContext.jsx
        │   ├── usePersistedState.js      # reload-safe state
        │   └── useConfirm.jsx
        ├── hooks/useSSE.js                # SSE + reconciling-poll fallback
        └── pages/
            ├── DashboardPage.jsx  PipelinesPage.jsx  DocumentStudioPage.jsx
            ├── PDFChatPage.jsx  BatchPage.jsx  SchedulesPage.jsx
            ├── HistoryPage.jsx  AgentHistoryPage.jsx  ActionHistoryPage.jsx
            ├── TrashPage.jsx  CredentialsSettingsPage.jsx  ApiKeysPage.jsx
            └── ...
```

---

## Getting Started

### Option A — Docker (recommended)

```bash
git clone https://github.com/Git-me-Harish/TextLens.git
cd TextLens

cp backend/.env.example backend/.env
# Edit backend/.env — set SECRET_KEY, ANTHROPIC_API_KEY at minimum

docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs

`docker-compose.yml` brings up: `postgres`, `redis`, `minio`, `backend`, `celery-worker`, `celery-actions` (dedicated queue for agentic actions), `celery-beat`, `frontend`.

### Option B — Local development

**Backend**
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
docker compose up postgres redis minio -d

cp .env.example .env
# Edit DATABASE_URL, REDIS_URL, SECRET_KEY, ANTHROPIC_API_KEY (see below)

alembic upgrade head
uvicorn app.main:app --reload
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

**Celery worker + beat**
```bash
cd backend
celery -A app.worker.celery_app worker --loglevel=info --pool=solo -Q default,ocr,agents,ingest,actions,webhooks   # Windows
celery -A app.worker.celery_app beat --loglevel=info
```

---

## Environment Variables

Full authoritative list lives in `backend/app/core/config.py`; `backend/.env.example` covers the common case. Only `SECRET_KEY` and `ANTHROPIC_API_KEY` are required to get a working dev instance — everything else has a sane local default or degrades gracefully (e.g. no `GROQ_API_KEY` means PDF chat is unavailable, not a crash).

**Core**

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Async Postgres connection string | `postgresql+asyncpg://postgres:password@localhost:5432/textlens` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `SECRET_KEY` | JWT signing secret | *(change in production)* |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | Token TTLs | `60` / `7` |
| `FRONTEND_URL` | Frontend origin (CORS, OAuth redirects) | `http://localhost:5173` |
| `ENVIRONMENT` | `development` / `production` | `development` |
| `RATE_LIMIT_PER_MINUTE` | Per-IP request cap | `30` |

**Google OAuth & Calendar**

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth2 app credentials |
| `GOOGLE_REDIRECT_URI` | Login OAuth callback |
| `GOOGLE_CALENDAR_REDIRECT_URI` | Calendar-connect OAuth callback |

**Storage (MinIO / S3-compatible)**

| Variable | Description | Default |
|---|---|---|
| `MINIO_ENDPOINT` / `MINIO_PUBLIC_URL` | Internal / browser-facing endpoint | `http://localhost:9000` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | Credentials | `minioadmin` |
| `MINIO_BUCKET` | Bucket name | `textlens` |
| `MAX_FILE_SIZE_MB` | Upload size limit | `50` |

**AI Providers**

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude — OCR reasoning, domain pipelines, agentic actions | — |
| `AGENT_MODEL` | Claude model used by every domain agent | `claude-sonnet-4-20250514` |
| `AGENT_MAX_ITERATIONS` / `AGENT_MAX_TOOL_CALLS` | Agent loop bounds | `15` / `30` |
| `GROQ_API_KEY` | PDF chat answering | — |
| `VOYAGE_API_KEY` / `VOYAGE_MODEL` | Chunk embeddings for RAG | — / `voyage-3` |
| `RESEND_API_KEY` / `FROM_EMAIL` | Transactional + agentic-action email | — |

**Agentic Action Layer**

| Variable | Description | Default |
|---|---|---|
| `MCP_ENCRYPTION_KEY` (+ `MCP_KEY_VERSION`, `MCP_ENCRYPTION_KEY_V1`) | AES-256-GCM key for encrypting stored MCP credentials, versioned for rotation | — |
| `APPROVAL_TOKEN_TTL_MINUTES` | HITL approval token lifetime | `15` |
| `INTERNAL_MCP_SHARED_SECRET` | Shared secret on every call to a self-hosted MCP proxy | — |
| `ACTION_CELERY_QUEUE` | Celery queue for action execution | `actions` |
| `GOOGLE_CALENDAR_MCP_URL`, `EMAIL_MCP_URL`, `PHARMACY_MCP_URL`, `JOB_BOARD_MCP_URL`, `ACCOUNTING_MCP_URL` | MCP endpoints — all self-mounted on this backend at `/mcp/*` by default | `http://localhost:8000/mcp/...` |

---

## API Reference

All routes are mounted under `/api/v1`, except the MCP proxies which self-mount at `/mcp/*`.

```
Auth
POST   /auth/register
POST   /auth/login
POST   /auth/refresh
GET    /auth/google/login
GET    /auth/google/callback
POST   /auth/forgot-password
POST   /auth/reset-password

Users
GET    /users/me
PATCH  /users/me
GET    /users/me/stats
GET    /users                            # admin only

Jobs (OCR / extraction)
POST   /jobs/upload
POST   /jobs/{source_job_id}/reuse
POST   /jobs/ask                         # one-shot Q&A over a job's text
GET    /jobs
GET    /jobs/{job_id}
GET    /jobs/{job_id}/download
POST   /jobs/{job_id}/retry
DELETE /jobs/{job_id}                    # → Trash

Document Studio
POST   /studio/merge
POST   /studio/combine
POST   /studio/split
POST   /studio/edit

Agents (domain pipelines)
GET    /agents/catalog
POST   /agents/classify
POST   /agents/run
GET    /agents
GET    /agents/{run_id}
POST   /agents/{run_id}/cancel
DELETE /agents/{run_id}                  # → Trash

Agentic Actions
GET    /actions/catalog
GET    /actions/agent-run/{agent_run_id}/available
POST   /actions/run
GET    /actions
GET    /actions/{action_run_id}
DELETE /actions/{action_run_id}          # cancel in-progress
GET    /actions/{action_run_id}/stream           (SSE)
POST   /actions/{action_run_id}/approval-token
POST   /actions/{action_run_id}/approve
POST   /actions/{action_run_id}/reject

Credentials (MCP integrations)
GET    /credentials
GET    /credentials/services
GET    /credentials/google_calendar/connect-url
GET    /credentials/google_calendar/callback
POST   /credentials
DELETE /credentials/{service_name}

Batch
POST   /batch
GET    /batch
GET    /batch/{batch_id}
GET    /batch/{batch_id}/results
DELETE /batch/{batch_id}                 # → Trash

PDF Chat
GET    /chat/documents                   # documents available to chat over
POST   /chat/sessions
POST   /chat/sessions/{session_id}/ask
POST   /chat/ask
GET    /chat/sessions
GET    /chat/sessions/{session_id}
DELETE /chat/sessions/{session_id}       # → Trash

Trash
GET    /trash
GET    /trash/count
POST   /trash/{type}/{id}/restore
DELETE /trash/{type}/{id}                # permanent
DELETE /trash                            # empty all

Google Drive
GET    /drive/files
POST   /drive/import
POST   /drive/export/{run_id}

Schedules
GET    /schedules
POST   /schedules
GET    /schedules/presets
PATCH  /schedules/{schedule_id}/toggle
DELETE /schedules/{schedule_id}

API Keys & Webhooks
POST   /keys
GET    /keys
PATCH  /keys/{key_id}
DELETE /keys/{key_id}
POST   /webhooks
GET    /webhooks
PATCH  /webhooks/{webhook_id}/toggle
GET    /webhooks/{webhook_id}/deliveries
DELETE /webhooks/{webhook_id}

Corrections, Audit, Notifications, Search, Analytics
POST   /agents/{run_id}/corrections
GET    /agents/{run_id}/corrections
GET    /audit
GET    /notifications
POST   /notifications/{id}/read
POST   /notifications/read-all
GET    /search
GET    /analytics/summary
GET    /analytics/timeline

Export
GET    /export/agent/{run_id}/csv
GET    /export/agent/{run_id}/excel

Real-time
GET    /sse/stream

Admin (admin role only)
GET    /admin/stats
GET    /admin/users
GET    /admin/users/{user_id}
PATCH  /admin/users/{user_id}
GET    /admin/jobs
GET    /admin/health

Health
GET    /health
```

---

## Security

- Passwords bcrypt-hashed; API keys stored as bcrypt hashes, plaintext shown once at creation
- JWT access + refresh tokens; Google's OAuth token is refreshed automatically at the credential-store layer before it expires
- MCP credentials (e.g. Google Calendar) encrypted at rest with AES-256-GCM, versioned for key rotation — decrypted in memory only for the duration of one tool call
- HITL approval tokens are short-lived, single-use signed JWTs scoped to one action run; re-issuing invalidates the previous token
- Per-MCP-service allowlist of callable tools, plus a Redis-backed circuit breaker shared across every worker process
- Per-IP rate limiting; uploads validated by MIME type and size
- CORS restricted to the configured frontend origin; SQL injection protected via SQLAlchemy's parameterized queries
- Webhook payloads HMAC-SHA256 signed
- Immutable audit log for sensitive actions; Trash keeps content recoverable for 30 days — API keys, webhooks, and credentials are explicitly excluded and stay hard-deleted

---

## Production Checklist

- [ ] Change `SECRET_KEY` and generate a fresh `MCP_ENCRYPTION_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`)
- [ ] Set `ENVIRONMENT=production`
- [ ] Configure real Google OAuth credentials and `INTERNAL_MCP_SHARED_SECRET`
- [ ] Point `MINIO_*` at a real S3/GCS-compatible bucket, not local disk
- [ ] Add HTTPS (Let's Encrypt / load balancer)
- [ ] Set up database backups
- [ ] Configure log aggregation
- [ ] Run `celery-worker`, `celery-actions`, and `celery-beat` as managed, monitored services
- [ ] Set up health checks and alerting
- [ ] Rotate and vault-manage `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `VOYAGE_API_KEY`, `RESEND_API_KEY`

---

## License

Proprietary — all rights reserved unless otherwise noted.
