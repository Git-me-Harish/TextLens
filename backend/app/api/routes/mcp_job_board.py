"""
Job Board MCP proxy — self-hosted stand-in, same pattern as mcp_email.py.

No real job-board partner account behind this (LinkedIn/Indeed-style
partner APIs are gated behind paid/approved access this project doesn't
have). Real, working implementation instead: search runs against a
curated listings catalog, and submitted applications actually persist
(JobApplication, models/action_models.py) with trackable status —
swapping in a genuine partner API later means replacing these handler
bodies, not the MCP contract or the calling agent code.

Contract (same as every self-hosted MCP proxy in this codebase):
    POST /call
    body:     {"tool": "<name>", "arguments": {...}}
    response: {"result": <any>, "error": <str | null>}

Deployment: mounted on this same backend at /mcp/job-board — set
JOB_BOARD_MCP_URL to this app's own base URL.

Auth: no per-user credential (registry.py: credential_key=None,
auth_strategy="none") — protected by X-Internal-MCP-Secret instead.
"""

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes.mcp_common import verify_internal_mcp_secret
from app.db.database import AsyncSessionLocal
from app.models.action_models import JobApplication

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp/job-board", tags=["MCP: Job Board"])


class MCPCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


def _error_response(message: str) -> dict:
    return {"result": None, "error": message}


def _ok_response(result: Any) -> dict:
    return {"result": result, "error": None}


# Curated listings catalog — a real job board's postings live on their
# side, not ours.
_JOB_CATALOG: list[dict] = [
    {"id": "job_001", "title": "Backend Engineer", "company": "Northwind Systems", "location": "Remote", "experience_level": "mid", "salary_range": "$95k-$120k", "skills": ["python", "postgresql", "docker", "fastapi"], "description": "Build and maintain REST APIs for a fintech data platform."},
    {"id": "job_002", "title": "Frontend Engineer", "company": "Beacon Labs", "location": "Remote", "experience_level": "mid", "salary_range": "$90k-$115k", "skills": ["react", "typescript", "css"], "description": "Own the customer-facing dashboard for a B2B SaaS product."},
    {"id": "job_003", "title": "Data Scientist", "company": "Meridian Analytics", "location": "New York, NY", "experience_level": "senior", "salary_range": "$130k-$160k", "skills": ["python", "pandas", "sql", "machine learning"], "description": "Lead churn-prediction modeling for a subscription business."},
    {"id": "job_004", "title": "DevOps Engineer", "company": "Northwind Systems", "location": "Remote", "experience_level": "senior", "salary_range": "$120k-$150k", "skills": ["kubernetes", "terraform", "aws", "ci/cd"], "description": "Own infrastructure and deployment pipelines across 3 environments."},
    {"id": "job_005", "title": "Junior Software Engineer", "company": "Beacon Labs", "location": "Austin, TX", "experience_level": "entry", "salary_range": "$70k-$85k", "skills": ["python", "javascript", "git"], "description": "Entry-level role rotating across frontend and backend teams."},
    {"id": "job_006", "title": "Product Manager", "company": "Meridian Analytics", "location": "Remote", "experience_level": "senior", "salary_range": "$125k-$155k", "skills": ["product strategy", "sql", "roadmapping"], "description": "Own the roadmap for the analytics platform's self-serve reporting."},
    {"id": "job_007", "title": "Machine Learning Engineer", "company": "Cascade AI", "location": "San Francisco, CA", "experience_level": "mid", "salary_range": "$115k-$145k", "skills": ["python", "pytorch", "machine learning", "docker"], "description": "Deploy and monitor production ML models for document understanding."},
    {"id": "job_008", "title": "QA Engineer", "company": "Cascade AI", "location": "Remote", "experience_level": "entry", "salary_range": "$65k-$80k", "skills": ["testing", "python", "selenium"], "description": "Build automated test coverage for a growing product suite."},
]


def _job_by_id(job_id: str) -> dict | None:
    return next((j for j in _JOB_CATALOG if j["id"] == job_id), None)


@router.post("/call", dependencies=[Depends(verify_internal_mcp_secret)])
async def call_tool(payload: MCPCallRequest):
    handlers = {
        "search_jobs": _search_jobs,
        "get_job_details": _get_job_details,
        "match_resume_to_job": _match_resume_to_job,
        "submit_application": _submit_application,
        "get_application_status": _get_application_status,
    }
    handler = handlers.get(payload.tool)
    if handler is None:
        return _error_response(f"Unknown tool '{payload.tool}'. Supported: {sorted(handlers)}")

    try:
        return await handler(payload.arguments)
    except Exception as exc:
        logger.error("mcp.job_board.unexpected_error", tool=payload.tool, error=str(exc))
        return _error_response(f"Unexpected error handling '{payload.tool}': {exc}")


async def _search_jobs(args: dict) -> dict:
    """Args: keywords (required, list[str]), location, experience_level, limit."""
    keywords = args.get("keywords")
    if not keywords or not isinstance(keywords, list):
        return _error_response("search_jobs requires a non-empty 'keywords' array.")
    keyword_set = {k.strip().lower() for k in keywords if k}
    location = (args.get("location") or "").strip().lower()
    exp_level = args.get("experience_level")
    limit = args.get("limit") or 10

    def score(job: dict) -> int:
        job_skills = {s.lower() for s in job["skills"]}
        return len(keyword_set & job_skills)

    candidates = [j for j in _JOB_CATALOG if score(j) > 0]
    if location:
        candidates = [j for j in candidates if location in j["location"].lower() or (location == "remote" and j["location"].lower() == "remote")]
    if exp_level:
        candidates = [j for j in candidates if j["experience_level"] == exp_level]
    candidates.sort(key=score, reverse=True)

    return _ok_response({
        "results": [
            {"job_id": j["id"], "title": j["title"], "company": j["company"],
             "location": j["location"], "experience_level": j["experience_level"],
             "salary_range": j["salary_range"], "matched_skills": sorted(keyword_set & {s.lower() for s in j["skills"]})}
            for j in candidates[:limit]
        ],
    })


async def _get_job_details(args: dict) -> dict:
    """Args: job_id (required)."""
    job_id = args.get("job_id")
    if not job_id:
        return _error_response("get_job_details requires 'job_id'.")
    job = _job_by_id(job_id)
    if not job:
        return _error_response(f"Job '{job_id}' not found.")
    return _ok_response(job)


async def _match_resume_to_job(args: dict) -> dict:
    """Args: job_id (required), resume_skills (required, list[str]), resume_experience_years."""
    job_id = args.get("job_id")
    resume_skills = args.get("resume_skills")
    if not job_id or not resume_skills:
        return _error_response("match_resume_to_job requires 'job_id' and 'resume_skills'.")
    job = _job_by_id(job_id)
    if not job:
        return _error_response(f"Job '{job_id}' not found.")

    job_skills = {s.lower() for s in job["skills"]}
    resume_set = {s.strip().lower() for s in resume_skills if s}
    overlap = job_skills & resume_set
    skill_score = round(100 * len(overlap) / max(len(job_skills), 1))

    exp_years = args.get("resume_experience_years")
    exp_bands = {"entry": (0, 2), "mid": (2, 6), "senior": (6, 100), "lead": (8, 100)}
    band = exp_bands.get(job["experience_level"], (0, 100))
    exp_fit = exp_years is not None and band[0] <= exp_years
    fit_score = round(skill_score * 0.75 + (25 if exp_fit else 10))

    return _ok_response({
        "job_id": job_id, "fit_score": min(fit_score, 100),
        "matched_skills": sorted(overlap), "missing_skills": sorted(job_skills - resume_set),
        "experience_level_match": exp_fit,
    })


async def _submit_application(args: dict) -> dict:
    """
    Args: job_id, cover_letter, applicant_name, applicant_email (all
    required), user_id (injected by career_agent.py's job-board dispatch).
    """
    required = ["job_id", "cover_letter", "applicant_name", "applicant_email"]
    missing = [f for f in required if not args.get(f)]
    if missing:
        return _error_response(f"submit_application missing required fields: {missing}")
    user_id = args.get("user_id")
    if not user_id:
        return _error_response("submit_application requires 'user_id' (internal — not an agent-supplied field).")

    job = _job_by_id(args["job_id"])
    if not job:
        return _error_response(f"Job '{args['job_id']}' not found.")

    async with AsyncSessionLocal() as db:
        # One application per (user, job) — resubmitting just updates it,
        # matching how a real job board would treat a duplicate apply.
        existing = (await db.execute(
            select(JobApplication).where(
                JobApplication.user_id == user_id, JobApplication.job_id == job["id"],
            )
        )).scalar_one_or_none()

        if existing:
            existing.cover_letter = args["cover_letter"]
            existing.applicant_name = args["applicant_name"]
            existing.applicant_email = args["applicant_email"]
            existing.updated_at = datetime.utcnow()
            application = existing
        else:
            application = JobApplication(
                user_id=user_id, job_id=job["id"], job_title=job["title"], company_name=job["company"],
                cover_letter=args["cover_letter"], applicant_name=args["applicant_name"],
                applicant_email=args["applicant_email"], status="submitted",
            )
            db.add(application)
        await db.commit()
        await db.refresh(application)

    logger.info("mcp.job_board.application_submitted", application_id=application.id, job_id=job["id"])
    return _ok_response({
        "application_id": application.id, "job_id": job["id"], "job_title": job["title"],
        "company_name": job["company"], "status": application.status,
    })


async def _get_application_status(args: dict) -> dict:
    """Args: application_id (required), user_id (injected)."""
    application_id, user_id = args.get("application_id"), args.get("user_id")
    if not application_id:
        return _error_response("get_application_status requires 'application_id'.")
    async with AsyncSessionLocal() as db:
        q = select(JobApplication).where(JobApplication.id == application_id)
        if user_id:
            q = q.where(JobApplication.user_id == user_id)
        application = (await db.execute(q)).scalar_one_or_none()
    if not application:
        return _error_response(f"Application '{application_id}' not found.")
    return _ok_response({
        "application_id": application.id, "job_title": application.job_title,
        "company_name": application.company_name, "status": application.status,
        "created_at": application.created_at.isoformat(),
    })
