"""
Career / HR Domain Agent

Handles all career document actions:
  - find_jobs              → Job Board MCP
  - match_resume           → Pure Claude reasoning
  - apply_to_job           → Job Board MCP + Email MCP
  - optimize_resume        → Pure Claude reasoning
  - generate_interview_prep → Pure Claude reasoning
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)

_NO_APPROVAL_ACTIONS = frozenset({"optimize_resume", "generate_interview_prep", "match_resume"})


def _flatten_skills(raw) -> list[str]:
    """
    agent_service.py's resume_parser emits skills as a nested object —
    {"technical": [...], "soft": [...], "tools": [...], "languages": [...]} —
    not a flat list. Flatten technical + tools (the two categories relevant to
    job matching) into one list; tolerate a flat list too in case the shape
    ever changes upstream.
    """
    if isinstance(raw, dict):
        return [*(raw.get("technical") or []), *(raw.get("tools") or [])]
    if isinstance(raw, list):
        return raw
    return []

_TOOLS: dict[str, list[dict]] = {

    "find_jobs": [
        {
            "name": "search_jobs",
            "description": "Search for job openings matching a skill set and experience level.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "Top 5 skills from the resume"},
                    "location": {"type": "string", "description": "City/country or 'remote'"},
                    "experience_level": {"type": "string", "enum": ["entry", "mid", "senior", "lead"]},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["keywords"],
            },
        },
        {
            "name": "match_resume_to_job",
            "description": "Score how well a resume matches a specific job listing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "resume_skills": {"type": "array", "items": {"type": "string"}},
                    "resume_experience_years": {"type": "number"},
                },
                "required": ["job_id", "resume_skills"],
            },
        },
    ],

    "apply_to_job": [
        {
            "name": "get_job_details",
            "description": "Fetch full details for a specific job listing.",
            "input_schema": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
        {
            "name": "submit_job_application",
            "description": "Submit a job application to the job board.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "cover_letter": {"type": "string", "description": "Tailored cover letter text"},
                    "applicant_name": {"type": "string"},
                    "applicant_email": {"type": "string"},
                },
                "required": ["job_id", "cover_letter", "applicant_name", "applicant_email"],
            },
        },
        {
            "name": "send_application_confirmation_email",
            "description": "Send a confirmation email to the applicant after applying.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string"},
                    "job_title": {"type": "string"},
                    "company_name": {"type": "string"},
                },
                "required": ["to_email", "job_title", "company_name"],
            },
        },
    ],

    "match_resume": [],
    "optimize_resume": [],
    "generate_interview_prep": [],
}


class CareerAgent(BaseAgent):
    DOMAIN = "hr"

    ACTION_PROMPTS = {
        "find_jobs": (
            "Extract the candidate's top skills, years of experience, current role, "
            "and location preference from the resume. Search for matching jobs, then rank "
            "the top 5 results by fit score. Return JSON with: 'candidate_summary' (1-2 "
            "sentences), 'jobs' (list of {title, company, location, fit_score (0-100), "
            "why_it_matches})."
        ),
        "match_resume": (
            "The user's instructions contain a target job description (or a job title/company "
            "to match against). If no job description was provided in the instructions, say so "
            "clearly in the summary and set match_score to null — do not guess a fictional job. "
            "Otherwise, compare the candidate's resume (skills, experience, education) against "
            "that job description in detail. Return JSON with: 'match_score' (0-100 or null), "
            "'matching_skills' (list), 'missing_skills' (list), 'strengths' (list), "
            "'gaps' (list), 'recommendation' (whether to apply, and how to strengthen the "
            "application before doing so)."
        ),
        "apply_to_job": (
            "Get the full job details, then craft a highly tailored cover letter "
            "based on the candidate's resume. Submit the application and send a "
            "confirmation email. Include the job title, company, and application date in the result."
        ),
        "optimize_resume": (
            "Analyse the resume in detail. Identify weak sections, missing keywords, "
            "formatting issues, and impact gaps. Return JSON with: 'overall_assessment' "
            "(2-3 sentences), 'ats_concerns' (list), 'section_feedback' (list of "
            "{section, issue, suggested_rewrite}), 'missing_keywords' (list), "
            "'priority_actions' (ordered list of the highest-impact changes to make first)."
        ),
        "generate_interview_prep": (
            "Based on the resume and target role (if specified in the instructions), generate "
            "a comprehensive interview preparation plan. Return JSON with: 'target_role', "
            "'likely_questions' (list of {category, question}), 'star_answers' (list of "
            "{question, situation, task, action, result} built from the candidate's actual "
            "experience — do not invent experience they don't have), 'technical_topics_to_review' "
            "(list), 'key_talking_points' (list)."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]
        skills = _flatten_skills(ctx.get("skills"))[:5]
        # The resume parser (agent_service.py) emits 'full_name', not 'candidate_name' —
        # keep the old key as a fallback in case a differently-shaped extraction ever lands here.
        name = ctx.get("full_name") or ctx.get("candidate_name") or "the candidate"

        if action_type == "find_jobs":
            steps = [
                ActionPlanStep(step_number=1, description=f"Search jobs matching skills: {', '.join(skills)}", requires_external_call=True, is_reversible=True, tool_name="search_jobs"),
                ActionPlanStep(step_number=2, description="Score top matches against resume", requires_external_call=True, is_reversible=True, tool_name="match_resume_to_job"),
            ]
            external = ["job_board_api"]
            data_used = {"skills": skills, "location": ctx.get("location"), "experience_years": ctx.get("total_experience_years")}
            risk = "low"

        elif action_type == "apply_to_job":
            # There's no persisted "search results" list to select from yet — find_jobs
            # and apply_to_job aren't wired together. Until that exists, the job to apply
            # to must be given explicitly via the instructions box.
            job_id = (state.get("user_context") or "").strip() or ctx.get("target_job_id", "")
            if not job_id:
                raise ValueError(
                    "apply_to_job needs to know which job to apply to. Run 'Find Relevant "
                    "Jobs' first, then re-run this action with the job ID or listing URL "
                    "in the instructions box."
                )
            steps = [
                ActionPlanStep(step_number=1, description="Fetch job details", requires_external_call=True, is_reversible=True, tool_name="get_job_details"),
                ActionPlanStep(step_number=2, description=f"Draft tailored cover letter for {name}", requires_external_call=False, is_reversible=True, tool_name=None),
                ActionPlanStep(step_number=3, description="Submit application to job board", requires_external_call=True, is_reversible=False, tool_name="submit_job_application"),
                ActionPlanStep(step_number=4, description="Send confirmation email to applicant", requires_external_call=True, is_reversible=True, tool_name="send_application_confirmation_email"),
            ]
            external = ["job_board_api", "email_api"]
            data_used = {"applicant_name": name, "applicant_email": ctx.get("email"), "target_job_id": job_id}
            risk = "medium"

        elif action_type == "match_resume":
            steps = [
                ActionPlanStep(step_number=1, description=f"Compare {name}'s resume against the provided job description", requires_external_call=False, is_reversible=True, tool_name=None),
            ]
            external = []
            data_used = {"skills": skills, "target_role": (state.get("user_context") or "")[:200]}
            risk = "low"

        else:
            steps = [
                ActionPlanStep(step_number=1, description=f"Analyse resume and produce {action_type.replace('_', ' ')}", requires_external_call=False, is_reversible=True, tool_name=None),
            ]
            external = []
            data_used = {}
            risk = "low"

        return ActionPlan(
            summary=f"Execute '{action_type}' for {name}.",
            steps=steps,
            estimated_duration_seconds=45 if external else 20,
            external_services=external,
            data_to_be_sent=data_used,
            risk_level=risk,
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        job_board_tools = {"search_jobs", "match_resume_to_job", "get_job_details", "submit_job_application"}
        email_tools = {"send_application_confirmation_email"}

        try:
            if tool_name in job_board_tools:
                mcp_map = {
                    "search_jobs": "search_jobs",
                    "match_resume_to_job": "match_resume_to_job",
                    "get_job_details": "get_job_details",
                    "submit_job_application": "submit_application",
                }
                creds = self.user_mcp_credentials.get("job_board_api")
                result = await call_mcp_tool("job_board_api", mcp_map[tool_name], tool_input, creds)

            elif tool_name in email_tools:
                creds = self.user_mcp_credentials.get("email_api")
                job_title = tool_input.get("job_title", "the role")
                company_name = tool_input.get("company_name", "the company")
                result = await call_mcp_tool("email_api", "send_email", {
                    "to": tool_input["to_email"],
                    "subject": f"Application Submitted — {job_title} at {company_name}",
                    "body": (
                        f"Hi,\n\nYour application for {job_title} at {company_name} "
                        f"has been submitted successfully. We'll follow up here with any updates.\n\n"
                        f"Good luck!"
                    ),
                }, creds)

            else:
                return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")

            return ToolResult(tool_name=tool_name, success=True, data=result)

        except Exception as exc:
            logger.error("career_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))
