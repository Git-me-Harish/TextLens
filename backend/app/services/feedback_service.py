"""
Correction feedback service.

Pulls recent human corrections for a domain+pipeline combination and formats
them as few-shot examples to inject into agent system prompts.

This makes the agent progressively better — every correction a user makes
directly improves future extractions on the same pipeline.
"""
import logging
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 5   # cap to avoid bloating the prompt


async def get_few_shot_examples(
    db: AsyncSession,
    domain: str,
    pipeline_type: str,
    limit: int = MAX_EXAMPLES,
) -> str:
    """
    Query the most recent corrections for this domain+pipeline,
    return a formatted string block to inject into the system prompt.
    Returns empty string if no corrections exist yet.
    """
    try:
        # Join field_corrections → agent_runs to filter by domain+pipeline
        result = await db.execute(
            text("""
                SELECT
                    fc.field_path,
                    fc.original_value,
                    fc.corrected_value,
                    ar.pipeline_type
                FROM field_corrections fc
                JOIN agent_runs ar ON fc.agent_run_id = ar.id
                WHERE ar.domain = :domain
                  AND ar.pipeline_type = :pipeline_type
                ORDER BY fc.created_at DESC
                LIMIT :limit
            """),
            {"domain": domain, "pipeline_type": pipeline_type, "limit": limit},
        )
        rows = result.fetchall()

        if not rows:
            return ""

        lines = []
        for row in rows:
            field = row.field_path.replace("_", " ")
            orig = row.original_value or "(empty)"
            corr = row.corrected_value
            lines.append(f'  • Field "{field}": was "{orig}" → corrected to "{corr}"')

        block = (
            "\n\n--- HUMAN CORRECTIONS FROM PREVIOUS RUNS ---\n"
            "The following corrections were made by users on similar documents.\n"
            "Use them to improve your extraction accuracy:\n"
            + "\n".join(lines)
            + "\n--- END CORRECTIONS ---"
        )
        return block

    except Exception as exc:
        # Never fail the agent run because of feedback lookup
        logger.warning(f"[feedback] could not load examples: {exc}")
        return ""