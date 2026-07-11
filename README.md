# TextLens : Intelligent Document Processing Platform

TextLens is a production-grade, full-stack document intelligence platform that combines classical OCR with LLM-powered AI agents to extract, structure, analyze, and converse with documents across multiple industry domains (Finance, Healthcare, Legal, Logistics, HR, Education, Government).

It supports single-document OCR tools, domain-specific AI extraction pipelines, bulk batch processing, scheduled/recurring automation, Google Drive integration, PDF chat (RAG-style Q&A), webhooks, an enterprise API-key layer, and a human-in-the-loop correction/audit trail.

---

## Table of Contents

1. [Key Features](#key-features)
2. [Tech Stack](#tech-stack)
3. [High-Level Architecture](#high-level-architecture)
4. [Request Flow](#request-flow)
5. [Domain Agent Pipelines](#domain-agent-pipelines)
6. [Data Model (ERD)](#data-model-erd)
7. [Background Processing & Scheduling](#background-processing--scheduling)
8. [Project Structure](#project-structure)
9. [Getting Started](#getting-started)
10. [Environment Variables](#environment-variables)
11. [API Reference](#api-reference)
12. [Security](#security)
13. [Production Checklist](#production-checklist)

---

## Key Features

### Core OCR Tools
- **Image OCR** — JPG/PNG/TIFF text extraction via Tesseract with a preprocessing pipeline (grayscale, deskew, denoise, adaptive threshold)
- **PDF Extract** — Structured heading/content extraction with font-based layout analysis, with automatic fallback from native text extraction to OCR for scanned PDFs
- **PDF Summarize** — Extractive document summarization
- **PDF → Word** — Export structured PDF content to `.docx`
- **PDF Chat** — Conversational Q&A over document content using retrieval-augmented prompting (Groq/Llama 3.3), with persisted chat sessions and auto-suggested starter questions

### AI Agent Pipelines (Domain Intelligence)
- 8 domains × multiple specialist pipelines (invoice processing, bank statement analysis, KYC, cheque parsing, medical records, prescriptions, lab reports, insurance claims, contract analysis, NDA review, court documents, due diligence, waybills, purchase orders, customs declarations, resume parsing, transcripts, certificates, tax forms, permits, regulatory filings, and more)
- Each pipeline returns a strict, typed JSON contract with confidence scores, anomaly flags, and a human-readable summary
- Automatic document classification — auto-detect the right domain/pipeline from extracted text

### Enterprise & Automation
- **Batch Processing** — Upload multiple files, process them through a single pipeline, track per-file status
- **Scheduled Batches** — Recurring cron-based jobs via Celery Beat, optionally pulling new files from a Google Drive folder each run
- **Google Drive Integration** — Import documents directly from Drive, export structured results back to Drive
- **Webhooks** — Register endpoints to receive `job.completed`, `agent.completed`, `batch.completed` events, HMAC-signed, with delivery history
- **API Keys** — Enterprise-grade API access (`tl_live_...`), bcrypt-hashed, usage-tracked, with monthly limits
- **Corrections & Audit Trail** — Human-in-the-loop field corrections that feed back into pipeline quality, plus an immutable, append-only audit log of all significant actions
- **Export** — CSV/Excel export of any structured agent result

### Platform
- **Auth** — Email/password + Google OAuth2, JWT access + refresh tokens, forgot/reset password flow
- **RBAC** — `admin` / `user` roles
- **Rate limiting** — Per-IP request throttling via SlowAPI
- **Async processing** — FastAPI BackgroundTasks for on-demand jobs, Celery + Redis for scheduled/recurring work

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend Framework | FastAPI (Python 3.11) |
| ORM / Migrations | SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 16 |
| Cache / Broker | Redis 7 |
| Task Queue | Celery (worker + beat scheduler) |
| OCR Engine | Tesseract, PyMuPDF (fitz), pytesseract, Pillow |
| Document Export | python-docx, ReportLab, openpyxl |
| AI / LLM | Anthropic Claude (agent extraction), Groq Llama 3.3 (PDF chat) |
| Frontend | React 18, Vite, React Router v6 |
| Frontend Libraries | axios, framer-motion, react-hook-form, react-dropzone, react-hot-toast, date-fns |
| Auth | JWT (python-jose), bcrypt (passlib), Google OAuth2 (authlib) |
| Infrastructure | Docker, Docker Compose, Nginx |

---

## High-Level Architecture

```mermaid
flowchart TB
    subgraph Client["Client Layer"]
        Browser["React SPA (Vite)"]
    end

    subgraph Edge["Edge / Reverse Proxy"]
        Nginx["Nginx (serves static build + reverse proxy)"]
    end

    subgraph API["Backend — FastAPI"]
        Auth["Auth Routes<br/>(JWT, OAuth2, RBAC)"]
        Jobs["Jobs Routes<br/>(OCR upload/status/download)"]
        Agents["Agents Routes<br/>(domain pipelines)"]
        Batch["Batch Routes"]
        Chat["Chat Routes<br/>(PDF Q&A)"]
        Drive["Drive Routes<br/>(Google Drive import/export)"]
        Schedules["Schedules Routes<br/>(cron automation)"]
        Keys["API Keys / Webhooks Routes"]
        Corrections["Corrections / Audit Routes"]
        Export["Export Routes<br/>(CSV/Excel)"]
    end

    subgraph Services["Service Layer"]
        OCRService["ocr_service.py<br/>Tesseract + PyMuPDF pipeline"]
        AgentService["agent_service.py<br/>Claude domain pipelines"]
        ChatService["chat_service.py<br/>Groq chunk-retrieval RAG"]
        BatchService["batch_service.py"]
        WebhookService["webhook_service.py<br/>HMAC signed delivery"]
        ExportService["export_service.py"]
        FeedbackService["feedback_service.py"]
    end

    subgraph Async["Async Processing"]
        BGTasks["FastAPI BackgroundTasks<br/>(on-demand jobs)"]
        CeleryWorker["Celery Worker"]
        CeleryBeat["Celery Beat<br/>(cron scheduler, every 60s)"]
    end

    subgraph Data["Data Layer"]
        Postgres[("PostgreSQL 16")]
        Redis[("Redis 7<br/>broker + result backend + cache")]
        Uploads[("Local / Object Storage<br/>uploaded files")]
    end

    subgraph External["External Services"]
        Claude["Anthropic Claude API"]
        Groq["Groq API (Llama 3.3)"]
        GoogleOAuth["Google OAuth2"]
        GoogleDrive["Google Drive API / MCP"]
    end

    Browser -->|HTTPS| Nginx
    Nginx --> Auth & Jobs & Agents & Batch & Chat & Drive & Schedules & Keys & Corrections & Export

    Jobs --> OCRService
    Agents --> AgentService
    Chat --> ChatService
    Batch --> BatchService
    Keys --> WebhookService
    Export --> ExportService
    Corrections --> FeedbackService

    Jobs --> BGTasks
    Batch --> BGTasks
    Agents --> BGTasks
    Schedules --> CeleryBeat
    CeleryBeat --> CeleryWorker
    CeleryWorker --> BatchService
    CeleryWorker --> Redis

    AgentService --> Claude
    ChatService --> Groq
    Auth --> GoogleOAuth
    Drive --> GoogleDrive

    OCRService --> Uploads
    BatchService --> Uploads

    API --> Postgres
    API --> Redis
    CeleryWorker --> Postgres
```

---

## Request Flow

Example: a user uploads an invoice and runs the Finance → Invoice Processor agent.

```mermaid
sequenceDiagram
    actor User
    participant FE as React Frontend
    participant API as FastAPI Backend
    participant DB as PostgreSQL
    participant OCR as OCR Service
    participant Agent as Agent Service
    participant Claude as Anthropic Claude
    participant WH as Webhook Service

    User->>FE: Upload invoice.pdf
    FE->>API: POST /api/jobs/upload
    API->>DB: Create OCRJob (status=pending)
    API-->>FE: 202 Accepted (job_id)
    API->>OCR: BackgroundTask: process_job()
    OCR->>OCR: PyMuPDF text extract / Tesseract OCR fallback
    OCR->>DB: Update OCRJob (status=completed, result_text)

    FE->>API: POST /api/agents/run (domain=finance, pipeline=invoice_processor)
    API->>DB: Create AgentRun (status=pending)
    API-->>FE: 202 Accepted (run_id)
    API->>Agent: BackgroundTask: run_agent()
    Agent->>Claude: Structured extraction prompt + document text
    Claude-->>Agent: Typed JSON result (vendor, totals, line items, confidence)
    Agent->>DB: Update AgentRun (status=completed, structured_result)
    Agent->>WH: fire_webhook("agent.completed")
    WH-->>User: (optional) POST to registered webhook URL

    FE->>API: GET /api/agents/{run_id} (poll)
    API-->>FE: AgentRun with structured_result
    FE->>User: Render structured invoice data + export options
```

---

## Domain Agent Pipelines

```mermaid
flowchart LR
    Doc["Extracted Document Text"] --> Classifier{"Auto-Classifier<br/>(optional)"}

    Classifier --> Finance
    Classifier --> Healthcare
    Classifier --> Legal
    Classifier --> Logistics
    Classifier --> HR
    Classifier --> Government
    Classifier --> General

    subgraph Finance["Finance"]
        F1["invoice_processor"]
        F2["bank_statement"]
        F3["kyc_document"]
        F4["cheque_parser"]
        F5["financial_report"]
    end

    subgraph Healthcare["Healthcare"]
        H1["medical_record"]
        H2["prescription"]
        H3["lab_report"]
        H4["insurance_claim"]
    end

    subgraph Legal["Legal"]
        L1["contract_analyzer"]
        L2["nda_analyzer"]
        L3["court_document"]
        L4["due_diligence"]
    end

    subgraph Logistics["Logistics"]
        LG1["waybill_parser"]
        LG2["purchase_order"]
        LG3["customs_declaration"]
        LG4["packing_list"]
    end

    subgraph HR["HR / Education"]
        HR1["resume_parser"]
        HR2["certificate_verifier"]
        HR3["transcript_analyzer"]
    end

    subgraph Government["Government / Compliance"]
        G1["tax_form"]
        G2["permit_license"]
        G3["regulatory_filing"]
    end

    subgraph General["General"]
        GE1["document_analyzer"]
    end

    Finance & Healthcare & Legal & Logistics & HR & Government & General --> Result["Typed JSON result<br/>+ confidence score<br/>+ anomaly flags<br/>+ human summary"]
    Result --> Corrections["Human Correction Loop"]
    Result --> Export["CSV / Excel Export"]
    Result --> WebhookOut["Webhook Notification"]
```

---

## Data Model (ERD)

```mermaid
erDiagram
    USER ||--o{ OCR_JOB : owns
    USER ||--o{ AGENT_RUN : owns
    USER ||--o{ BATCH_JOB : owns
    USER ||--o{ API_KEY : owns
    USER ||--o{ WEBHOOK : owns
    USER ||--o{ AUDIT_LOG : generates
    USER ||--o{ PASSWORD_RESET_TOKEN : requests
    USER ||--o{ SCHEDULED_BATCH : configures
    USER ||--o{ CHAT_SESSION : starts

    OCR_JOB ||--o{ AGENT_RUN : "feeds"
    OCR_JOB ||--o| BATCH_ITEM : "linked to"
    OCR_JOB ||--o{ CHAT_SESSION : "discussed in"

    BATCH_JOB ||--o{ BATCH_ITEM : contains

    AGENT_RUN ||--o{ FIELD_CORRECTION : "corrected by"
    API_KEY ||--o{ AGENT_RUN : authorizes

    WEBHOOK ||--o{ WEBHOOK_DELIVERY : logs

    USER {
        uuid id PK
        string email UK
        string full_name
        string hashed_password
        string google_id UK
        string role
        bool is_active
        bool is_verified
    }

    OCR_JOB {
        uuid id PK
        uuid user_id FK
        string job_type
        string status
        string original_filename
        text result_text
        string file_hash
        uuid batch_item_id FK
    }

    AGENT_RUN {
        uuid id PK
        uuid user_id FK
        uuid ocr_job_id FK
        string domain
        string pipeline_type
        string status
        json structured_result
        int confidence_score
        uuid api_key_id FK
    }

    BATCH_JOB {
        uuid id PK
        uuid user_id FK
        string name
        string domain
        string status
        int total_files
        int completed_files
        int failed_files
    }

    BATCH_ITEM {
        uuid id PK
        uuid batch_job_id FK
        string original_filename
        string status
    }

    API_KEY {
        uuid id PK
        uuid user_id FK
        string name
        string key_prefix
        string key_hash UK
        bool is_active
        int monthly_limit
    }

    WEBHOOK {
        uuid id PK
        uuid user_id FK
        string target_url
        json events
        bool is_active
    }

    WEBHOOK_DELIVERY {
        uuid id PK
        uuid webhook_id FK
        string event
        int status_code
        bool success
    }

    AUDIT_LOG {
        uuid id PK
        uuid user_id FK
        string action
        string entity_type
        uuid entity_id
    }

    FIELD_CORRECTION {
        uuid id PK
        uuid agent_run_id FK
        string field_path
        text original_value
        text corrected_value
    }

    SCHEDULED_BATCH {
        uuid id PK
        uuid user_id FK
        string cron_expr
        string domain
        string drive_folder_id
        bool is_active
        datetime next_run_at
    }

    CHAT_SESSION {
        uuid id PK
        uuid user_id FK
        uuid job_id FK
        json messages
        json suggested_questions
    }

    PASSWORD_RESET_TOKEN {
        uuid id PK
        uuid user_id FK
        string token UK
        datetime expires_at
        bool used
    }
```

---

## Background Processing & Scheduling

```mermaid
flowchart TD
    subgraph OnDemand["On-Demand Jobs"]
        Upload["User uploads file / runs agent"] --> BG["FastAPI BackgroundTasks"]
        BG --> Process["OCR / Agent processing"]
        Process --> UpdateDB1["Update job/run status in Postgres"]
    end

    subgraph Recurring["Recurring / Automated Jobs"]
        Beat["Celery Beat<br/>ticks every 60s"] --> Check["check_and_dispatch_schedules"]
        Check --> Query["Query ScheduledBatch WHERE<br/>is_active=true AND next_run_at <= now"]
        Query --> Dispatch["Enqueue process_scheduled_batch task"]
        Dispatch --> Worker["Celery Worker"]
        Worker --> DrivePull["Pull new files from Drive folder<br/>(if configured)"]
        DrivePull --> CreateBatch["Create BatchJob + BatchItems"]
        CreateBatch --> RunPipeline["Run configured domain pipeline<br/>on each file"]
        RunPipeline --> UpdateDB2["Update BatchJob / OCRJob / AgentRun"]
        UpdateDB2 --> NextRun["Compute next_run_at from cron_expr"]
    end

    UpdateDB1 --> Notify["fire_webhook() → registered webhook URLs"]
    UpdateDB2 --> Notify

    Redis[("Redis<br/>Celery broker + result backend")] -.-> Beat
    Redis -.-> Worker
```

---

## Project Structure

```
TextLens/
├── docker-compose.yml
├── README.md
├── Redis.md                      # Windows Redis setup notes
├── .gitignore
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                # FastAPI app, router registration, health checks
│       ├── api/
│       │   ├── deps.py            # get_current_user, require_admin, DB session deps
│       │   └── routes/
│       │       ├── auth.py        # register/login/refresh/OAuth/password reset
│       │       ├── users.py       # profile, stats, admin user list
│       │       ├── jobs.py        # OCR job upload/status/download/retry
│       │       ├── agents.py      # domain pipeline execution, catalog, classify
│       │       ├── batch.py       # bulk file processing
│       │       ├── chat.py        # PDF chat sessions
│       │       ├── drive.py       # Google Drive import/export
│       │       ├── schedules.py   # recurring batch automation
│       │       ├── apikeys.py     # API keys + webhooks management
│       │       ├── corrections.py # field corrections + audit log
│       │       └── export.py      # CSV / Excel export
│       ├── core/
│       │   ├── config.py          # pydantic Settings (env-driven)
│       │   └── security.py        # JWT + bcrypt helpers
│       ├── db/
│       │   ├── database.py        # async SQLAlchemy engine/session
│       │   └── redis.py           # Redis client
│       ├── models/
│       │   └── models.py          # SQLAlchemy ORM models (all entities)
│       ├── schemas/
│       │   └── schemas.py         # Pydantic request/response schemas
│       ├── services/
│       │   ├── ocr_service.py     # Tesseract + PyMuPDF OCR pipeline
│       │   ├── agent_service.py   # Claude domain pipelines + prompts
│       │   ├── chat_service.py    # Groq chunk-retrieval RAG chat
│       │   ├── batch_service.py   # bulk processing orchestration
│       │   ├── webhook_service.py # HMAC-signed webhook delivery
│       │   ├── export_service.py  # CSV/Excel generation
│       │   └── feedback_service.py# correction feedback loop
│       └── worker/
│           ├── celery_app.py      # Celery app + beat schedule config
│           └── tasks.py           # scheduled batch dispatch + execution tasks
│
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    ├── vite.config.js
    ├── package.json
    ├── index.html
    ├── public/
    └── src/
        ├── main.jsx
        ├── App.jsx                # route definitions
        ├── components/
        │   ├── layout/             # app shell, nav, sidebar
        │   ├── ocr/                # OCR tool widgets
        │   ├── ui/                 # shared UI primitives
        │   ├── DrivePickerModal.jsx
        │   └── ProtectedRoute.jsx
        ├── lib/
        │   ├── api.js              # axios instance + auto token refresh
        │   ├── AuthContext.jsx     # auth state/provider
        │   └── AgentContext.jsx    # active-agent run state/provider
        ├── pages/                  # Dashboard, History, Tools, Batch, Chat, Schedules, etc.
        └── styles/                 # design tokens + component CSS
```

---

## Getting Started

### Option A — Docker (recommended)

```bash
git clone https://github.com/Git-me-Harish/TextLens.git
cd TextLens

# Configure backend environment
cp backend/.env.example backend/.env
# Edit backend/.env — set SECRET_KEY, GOOGLE_CLIENT_ID/SECRET, ANTHROPIC_API_KEY, GROQ_API_KEY

docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs

### Option B — Local development

**Backend**

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt

# Start Postgres + Redis (via Docker, or install natively)
docker compose up postgres redis -d

cp .env.example .env
# Edit DATABASE_URL, REDIS_URL, SECRET_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY

uvicorn app.main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

**Celery worker + beat** (for scheduled batches — see [Redis.md](Redis.md) for Windows Redis setup)

```bash
cd backend
celery -A app.worker.celery_app worker --loglevel=info --pool=solo   # Windows
celery -A app.worker.celery_app beat --loglevel=info
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | Async Postgres connection string | `postgresql+asyncpg://postgres:password@localhost:5432/textlens` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `SECRET_KEY` | JWT signing secret | *(change in production)* |
| `ALGORITHM` | JWT algorithm | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token TTL | `60` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token TTL | `7` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth2 credentials | — |
| `GOOGLE_REDIRECT_URI` | OAuth2 callback URL | `http://localhost:8000/api/auth/google/callback` |
| `FRONTEND_URL` | Frontend origin (CORS) | `http://localhost:5173` |
| `UPLOAD_DIR` | Local file storage path | `./uploads` |
| `MAX_FILE_SIZE_MB` | Upload size limit | `50` |
| `RATE_LIMIT_PER_MINUTE` | Per-IP rate limit | `30` |
| `ENVIRONMENT` | `development` / `production` | `development` |
| `ANTHROPIC_API_KEY` | Claude API key (agent pipelines) | — |
| `GROQ_API_KEY` | Groq API key (PDF chat) | — |

---

## API Reference

```
Auth
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/refresh
GET    /api/auth/google/login
GET    /api/auth/google/callback
POST   /api/auth/forgot-password
POST   /api/auth/reset-password

Users
GET    /api/users/me
PATCH  /api/users/me
GET    /api/users/me/stats
GET    /api/users                        (admin only)

OCR Jobs
POST   /api/jobs/upload
POST   /api/jobs/{source_job_id}/reuse
POST   /api/jobs/ask
GET    /api/jobs
GET    /api/jobs/{job_id}
GET    /api/jobs/{job_id}/download
POST   /api/jobs/{job_id}/retry
DELETE /api/jobs/{job_id}

Agents (domain pipelines)
GET    /api/agents/catalog
POST   /api/agents/classify
POST   /api/agents/run
GET    /api/agents
GET    /api/agents/{run_id}
DELETE /api/agents/{run_id}

Batch
POST   /api/batch
GET    /api/batch
GET    /api/batch/{batch_id}
GET    /api/batch/{batch_id}/results
DELETE /api/batch/{batch_id}

PDF Chat
POST   /api/chat/sessions
POST   /api/chat/ask
GET    /api/chat/sessions
GET    /api/chat/sessions/{session_id}
DELETE /api/chat/sessions/{session_id}

Google Drive
GET    /api/drive/files
POST   /api/drive/import
POST   /api/drive/export/{run_id}

Schedules
GET    /api/schedules
POST   /api/schedules
GET    /api/schedules/presets
PATCH  /api/schedules/{schedule_id}/toggle
DELETE /api/schedules/{schedule_id}

API Keys & Webhooks
POST   /api/keys
GET    /api/keys
PATCH  /api/keys/{key_id}
DELETE /api/keys/{key_id}
POST   /api/webhooks
GET    /api/webhooks
PATCH  /api/webhooks/{webhook_id}/toggle
GET    /api/webhooks/{webhook_id}/deliveries
DELETE /api/webhooks/{webhook_id}

Corrections & Audit
POST   /api/agents/{run_id}/corrections
GET    /api/agents/{run_id}/corrections
GET    /api/audit

Export
GET    /api/export/agent/{run_id}/csv
GET    /api/export/agent/{run_id}/excel

Health
GET    /health
GET    /health/deps
GET    /health/test-ocr
```

---

## Security

- Passwords hashed with bcrypt (cost factor 12)
- JWT access tokens (60 min) + refresh tokens (7 days)
- API keys stored as bcrypt hashes; plaintext shown only once at creation
- Per-IP rate limiting (configurable, default 30 req/min)
- File uploads validated by MIME type and size
- Files stored per-user in isolated directories
- CORS restricted to configured frontend origin
- SQL injection protected via SQLAlchemy ORM (parameterized queries)
- Webhook payloads signed with HMAC-SHA256
- Immutable, append-only audit log for all sensitive actions

---

## Production Checklist

- [ ] Change `SECRET_KEY` to a strong random value
- [ ] Set `ENVIRONMENT=production`
- [ ] Configure real Google OAuth credentials
- [ ] Set up an email service for password reset (SMTP/SendGrid)
- [ ] Use S3/GCS for file storage instead of local disk
- [ ] Add HTTPS (Let's Encrypt / load balancer)
- [ ] Set up database backups
- [ ] Configure log aggregation (Loki, Datadog, etc.)
- [ ] Run Celery worker + beat as managed, monitored services
- [ ] Set up health checks and alerting
- [ ] Rotate and vault-manage `ANTHROPIC_API_KEY` / `GROQ_API_KEY`

---

## License

Proprietary — all rights reserved unless otherwise noted.
