"""
Full-text document search — PostgreSQL tsvector via GIN indexes from migration 003.

Endpoint

  GET /search?q=query&type=all|jobs|agents&page=1&per_page=20

How it works:
  - plainto_tsquery('english', query) tokenises + stems the query (handles
    "invoices" → "invoic" matching "invoice")
  - @@ operator does the set intersection against the GIN index — O(log N)
  - ts_rank() scores results by how well they match
  - ts_headline() extracts the most relevant excerpt with search terms wrapped
    in <mark> tags for frontend highlighting

Results are scoped strictly to the authenticated user — no cross-user leakage.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import User

router = APIRouter(prefix="/search", tags=["search"])

_MAX_QUERY_LEN = 200
_HEADLINE_OPTS = "MaxWords=25, MinWords=10, ShortWord=3, HighlightAll=false, StartSel=<mark>, StopSel=</mark>"


#  OCR job search
async def _search_jobs(
    db: AsyncSession, user_id: str, tsq: str, limit: int, offset: int
):
    rows = await db.execute(
        text("""
            SELECT
                id,
                original_filename,
                job_type,
                status,
                page_count,
                processing_time_ms,
                created_at,
                completed_at,
                ts_headline(
                    'english', coalesce(result_text, ''),
                    plainto_tsquery('english', :q),
                    :opts
                ) AS excerpt,
                ts_rank(
                    to_tsvector('english', coalesce(result_text, '')),
                    plainto_tsquery('english', :q)
                ) AS rank
            FROM ocr_jobs
            WHERE
                user_id = :uid
                AND status = 'completed'
                AND (
                    to_tsvector('english', coalesce(result_text, ''))
                        @@ plainto_tsquery('english', :q)
                    OR
                    to_tsvector('simple', coalesce(original_filename, ''))
                        @@ plainto_tsquery('simple', :q)
                )
            ORDER BY rank DESC, created_at DESC
            LIMIT  :limit
            OFFSET :offset
        """),
        {
            "uid": user_id,
            "q": tsq,
            "opts": _HEADLINE_OPTS,
            "limit": limit,
            "offset": offset,
        },
    )
    return [
        {
            "type": "job",
            "id": str(r.id),
            "title": r.original_filename,
            "job_type": r.job_type,
            "status": r.status,
            "page_count": r.page_count,
            "processing_time_ms": r.processing_time_ms,
            "excerpt": r.excerpt or "",
            "relevance": round(float(r.rank), 4),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]


async def _count_jobs(db: AsyncSession, user_id: str, tsq: str) -> int:
    row = await db.execute(
        text("""
            SELECT COUNT(*) FROM ocr_jobs
            WHERE user_id = :uid
              AND status  = 'completed'
              AND (
                  to_tsvector('english', coalesce(result_text, ''))
                      @@ plainto_tsquery('english', :q)
                  OR
                  to_tsvector('simple', coalesce(original_filename, ''))
                      @@ plainto_tsquery('simple', :q)
              )
        """),
        {"uid": user_id, "q": tsq},
    )
    return int(row.scalar() or 0)


#  Agent run search


async def _search_agents(
    db: AsyncSession, user_id: str, tsq: str, limit: int, offset: int
):
    rows = await db.execute(
        text("""
            SELECT
                id,
                domain,
                pipeline_type,
                status,
                original_filename,
                confidence_score,
                processing_time_ms,
                created_at,
                completed_at,
                ts_headline(
                    'english',
                    coalesce(summary, '') || ' ' || coalesce(input_text, ''),
                    plainto_tsquery('english', :q),
                    :opts
                ) AS excerpt,
                ts_rank(
                    to_tsvector('english',
                        coalesce(summary, '') || ' ' || coalesce(input_text, '')),
                    plainto_tsquery('english', :q)
                ) AS rank
            FROM agent_runs
            WHERE
                user_id = :uid
                AND status = 'completed'
                AND to_tsvector('english',
                        coalesce(summary, '') || ' ' || coalesce(input_text, ''))
                    @@ plainto_tsquery('english', :q)
            ORDER BY rank DESC, created_at DESC
            LIMIT  :limit
            OFFSET :offset
        """),
        {
            "uid": user_id,
            "q": tsq,
            "opts": _HEADLINE_OPTS,
            "limit": limit,
            "offset": offset,
        },
    )
    return [
        {
            "type": "agent",
            "id": str(r.id),
            "title": r.original_filename or "Agent run",
            "domain": r.domain,
            "pipeline_type": r.pipeline_type,
            "status": r.status,
            "confidence_score": r.confidence_score,
            "processing_time_ms": r.processing_time_ms,
            "excerpt": r.excerpt or "",
            "relevance": round(float(r.rank), 4),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]


async def _count_agents(db: AsyncSession, user_id: str, tsq: str) -> int:
    row = await db.execute(
        text("""
            SELECT COUNT(*) FROM agent_runs
            WHERE user_id = :uid
              AND status  = 'completed'
              AND to_tsvector('english',
                      coalesce(summary, '') || ' ' || coalesce(input_text, ''))
                  @@ plainto_tsquery('english', :q)
        """),
        {"uid": user_id, "q": tsq},
    )
    return int(row.scalar() or 0)


#  Route
@router.get("")
async def search(
    q: str = Query(
        ..., min_length=2, max_length=_MAX_QUERY_LEN, description="Search query"
    ),
    type: str = Query(default="all", pattern="^(all|jobs|agents)$"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Full-text search across extracted documents and agent pipeline results.

    Query tips:
      - Multi-word: "invoice amount" finds docs containing both
      - Stemming: "invoices" matches "invoice", "invoiced"
      - Filename search: works on job results (type=jobs or type=all)

    Results include highlighted excerpts with <mark> tags around matched terms.
    """
    uid = user.id
    offset = (page - 1) * per_page
    tsq = q.strip()

    if not tsq:
        raise HTTPException(400, "Query cannot be empty")

    jobs: list = []
    agents: list = []
    jobs_total: int = 0
    agents_total: int = 0

    if type in ("all", "jobs"):
        jobs = await _search_jobs(db, uid, tsq, limit=per_page, offset=offset)
        jobs_total = await _count_jobs(db, uid, tsq)

    if type in ("all", "agents"):
        agents = await _search_agents(db, uid, tsq, limit=per_page, offset=offset)
        agents_total = await _count_agents(db, uid, tsq)

    if type == "all":
        # Merge and re-sort by relevance when returning both
        combined = sorted(jobs + agents, key=lambda x: x["relevance"], reverse=True)
        total = jobs_total + agents_total
        results = combined[:per_page]
    elif type == "jobs":
        results = jobs
        total = jobs_total
    else:
        results = agents
        total = agents_total

    return {
        "query": tsq,
        "type": type,
        "total": total,
        "page": page,
        "per_page": per_page,
        "results": results,
    }
