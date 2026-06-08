# TextLens Document Intelligence Platform

Production-grade OCR platform. Extract, analyze, transform documents.

## Features

- **Image OCR** — JPG/PNG/TIFF text extraction via Tesseract
- **PDF Extract** — Structured heading/content extraction with font analysis
- **PDF Summarize** — Extractive summarization
- **PDF to Word** — Export structured PDF content to .docx
- **PDF Chat** — Ask questions against document content (keyword matching)
- **Auth** — Email/password + Google OAuth2, JWT with refresh tokens, forgot password
- **RBAC** — admin / user roles
- **Rate limiting** — per-IP via slowapi
- **Async background jobs** — FastAPI BackgroundTasks + polling

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Python 3.11, SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| OCR | Tesseract, PyMuPDF, pytesseract |
| Frontend | React 18, Vite, React Router v6 |
| Auth | JWT (python-jose), bcrypt, Google OAuth2 |
| Infra | Docker Compose, Nginx |

## Quick start (Docker)

```bash
git clone <repo>
cd textlens

# Copy and configure env
cp backend/.env.example backend/.env
# Edit backend/.env — set SECRET_KEY, GOOGLE_CLIENT_ID/SECRET if needed

docker compose up --build
```

App available at:
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

## Local dev (no Docker)

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# Start Postgres + Redis first (or use Docker for just those)
docker compose up postgres redis -d

cp .env.example .env
# Edit DATABASE_URL, REDIS_URL, SECRET_KEY

uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Architecture

```
textlens/
  backend/
    app/
      api/routes/    # auth, jobs, users
      core/          # config, security (JWT/bcrypt)
      db/            # SQLAlchemy engine, Redis client
      models/        # SQLAlchemy ORM models
      schemas/       # Pydantic request/response schemas
      services/      # OCR processing logic
    Dockerfile
    requirements.txt
  frontend/
    src/
      components/    # layout, ocr, ui primitives
      lib/           # api client, auth context
      pages/         # all page components
      styles/        # CSS design tokens + components
    vite.config.js
    Dockerfile
    nginx.conf
  docker-compose.yml
```

## Database schema

### users
- id (UUID PK), email (unique), full_name, hashed_password, google_id, avatar_url
- role (admin|user), is_active, is_verified, created_at, updated_at

### ocr_jobs
- id (UUID PK), user_id (FK), job_type, status, original_filename, file_path
- result_text, result_file_path, error_message, page_count, processing_time_ms
- created_at, completed_at

### password_reset_tokens
- id, user_id (FK), token (unique), expires_at, used, created_at

## Security considerations

- Passwords hashed with bcrypt (cost factor 12)
- JWT access tokens expire in 60min, refresh tokens in 7 days
- Rate limited to 30 req/min per IP (configurable)
- File uploads validated by MIME type + size
- Files stored per-user in isolated directories
- CORS restricted to frontend origin
- SQL injection protected via SQLAlchemy ORM
- No file paths exposed directly to users (served via job ID)

## Production checklist

- [ ] Change `SECRET_KEY` in .env to a strong random value
- [ ] Set `ENVIRONMENT=production`  
- [ ] Configure real Google OAuth credentials
- [ ] Set up email service for password reset (SMTP/SendGrid)
- [ ] Use S3/GCS for file storage instead of local disk
- [ ] Add HTTPS (Let's Encrypt via Certbot or load balancer)
- [ ] Set up database backups
- [ ] Configure log aggregation (Loki, Datadog, etc.)
- [ ] Add Celery worker for heavy jobs (replace BackgroundTasks)
- [ ] Set up health checks and alerting

## API endpoints

```
POST   /api/auth/register
POST   /api/auth/login
POST   /api/auth/refresh
GET    /api/auth/google/login
GET    /api/auth/google/callback
POST   /api/auth/forgot-password
POST   /api/auth/reset-password

GET    /api/users/me
PATCH  /api/users/me
GET    /api/users/me/stats
GET    /api/users              (admin only)

POST   /api/jobs/upload
POST   /api/jobs/ask
GET    /api/jobs
GET    /api/jobs/:id
GET    /api/jobs/:id/download
DELETE /api/jobs/:id
```
