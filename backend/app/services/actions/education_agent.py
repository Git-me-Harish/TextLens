"""
Education Domain Agent

Handles all education/knowledge document actions:
  - summarize_document       → Pure Claude reasoning
  - generate_study_material  → Pure Claude reasoning
  - document_qa              → Pure Claude reasoning
  - generate_quiz            → Pure Claude reasoning
  - create_learning_plan     → Pure Claude reasoning
  - schedule_study_sessions  → Google Calendar MCP
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)

# Every action except schedule_study_sessions is read-only reasoning
_NO_APPROVAL_ACTIONS = frozenset({
    "summarize_document",
    "generate_study_material",
    "document_qa",
    "generate_quiz",
    "create_learning_plan",
})

_TOOLS: dict[str, list[dict]] = {
    "schedule_study_sessions": [
        {
            "name": "create_study_session_event",
            "description": "Block time on the user's calendar for one study session/milestone.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "e.g. 'Study: Chapter 3 — Cell Biology'"},
                    "start_datetime": {"type": "string", "description": "ISO 8601 datetime"},
                    "duration_minutes": {"type": "integer", "default": 60},
                    "notes": {"type": "string", "description": "What to cover in this session"},
                },
                "required": ["title", "start_datetime"],
            },
        },
        {
            "name": "check_study_availability",
            "description": "Check the user's calendar for free slots over the next 14 days, to place sessions where there's actually room.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "preferred_date": {"type": "string", "description": "ISO 8601 date to check first, if the user gave one. Leave null to search the next 14 days."},
                    "duration_minutes": {"type": "integer", "default": 60},
                },
                "required": [],
            },
        },
    ],
}


class EducationAgent(BaseAgent):
    DOMAIN = "education"

    ACTION_PROMPTS = {
        "summarize_document": (
            "Produce a clear summary of this educational document. "
            "Structure your response as JSON with: "
            "'document_type' (research paper/textbook chapter/lecture notes/certificate/assignment/etc), "
            "'subject_area', "
            "'title_or_topic', "
            "'author_or_source', "
            "'key_points' (list of the main ideas, 3-8 items), "
            "'summary' (3-5 sentences), "
            "'important_terms' (list of key terms/concepts defined or used). "
            "Use language appropriate for a student encountering this material."
        ),
        "generate_study_material": (
            "Turn this educational document into structured study material. "
            "Return JSON with: "
            "'topic', "
            "'key_concepts' (list of {term, definition}), "
            "'summary_notes' (bullet-point revision notes covering the whole document), "
            "'flashcards' (list of {question, answer}, 5-10 items), "
            "'further_reading_suggestions' (list of related topics worth studying next). "
            "Prioritise clarity and retention over exhaustiveness."
        ),
        "document_qa": (
            "You are a patient, knowledgeable tutor. "
            "Answer the user's question about this educational document clearly and accurately, "
            "grounded strictly in the document's content. "
            "Cite the specific section, page, or heading your answer is based on where possible. "
            "If the document does not answer the question, say so explicitly rather than guessing. "
            "Where useful, briefly explain the reasoning, not just the answer."
        ),
        "generate_quiz": (
            "Create a quiz to test understanding of this document. "
            "Return JSON with: "
            "'topic', "
            "'questions' (list of 5-10 items, each with: "
            "'question', 'type' (multiple_choice/true_false/short_answer), "
            "'options' (list, only for multiple_choice), 'correct_answer', "
            "'explanation' (why that answer is correct)), "
            "'difficulty' (beginner/intermediate/advanced) based on the document's content. "
            "Cover the material broadly rather than testing one narrow detail repeatedly."
        ),
        "create_learning_plan": (
            "Create a personalized learning plan based on this document. "
            "Return JSON with: "
            "'goal' (what mastering this material enables), "
            "'estimated_total_hours', "
            "'milestones' (ordered list of {milestone, description, estimated_hours, "
            "recommended_resources}), "
            "'prerequisites' (list of concepts the learner should already know), "
            "'suggested_practice' (list of exercises or activities to reinforce learning). "
            "Sequence milestones from foundational to advanced."
        ),
        "schedule_study_sessions": (
            "Break this material into study sessions (reuse a prior learning plan's milestones "
            "if the instructions reference one, otherwise derive 3-6 sessions from the document "
            "yourself). Check calendar availability, then create one calendar event per session, "
            "spaced out sensibly (not all on one day) over the next 1-2 weeks unless the user's "
            "instructions say otherwise. Return JSON with: 'topic', 'sessions_scheduled' "
            "(list of {title, start, end, covers})."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]
        doc_type = ctx.get("document_type", "educational document")
        topic = ctx.get("title_or_topic") or ctx.get("subject_area", "the material")

        descriptions = {
            "summarize_document": f"Summarise {doc_type} covering {topic}",
            "generate_study_material": f"Generate study notes and flashcards from {doc_type}",
            "document_qa": "Answer user question about the educational document",
            "generate_quiz": f"Generate a quiz covering {doc_type}",
            "create_learning_plan": f"Create a personalized learning plan based on {doc_type}",
        }

        if action_type == "schedule_study_sessions":
            steps = [
                ActionPlanStep(step_number=1, description="Check calendar availability", requires_external_call=True, is_reversible=True, tool_name="check_study_availability"),
                ActionPlanStep(step_number=2, description=f"Create study session events for {topic}", requires_external_call=True, is_reversible=True, tool_name="create_study_session_event"),
            ]
            external = ["google_calendar"]
            data_used = {"topic": topic, "instructions": (state.get("user_context") or "")[:300]}
            risk = "low"
        else:
            steps = [
                ActionPlanStep(
                    step_number=1,
                    description=descriptions.get(action_type, f"Process {action_type}"),
                    requires_external_call=False,
                    is_reversible=True,
                    tool_name=None,
                )
            ]
            external = []
            data_used = {}
            risk = "low"

        return ActionPlan(
            summary=f"Execute '{action_type}' on {doc_type}.",
            steps=steps,
            estimated_duration_seconds=45 if external else 15,
            external_services=external,
            data_to_be_sent=data_used,
            risk_level=risk,
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        calendar_tools = {"create_study_session_event", "check_study_availability"}

        try:
            if tool_name in calendar_tools:
                creds = self.user_mcp_credentials.get("google_calendar")
                if tool_name == "check_study_availability":
                    mcp_input = {
                        "preferred_date": tool_input.get("preferred_date"),
                        "duration_minutes": tool_input.get("duration_minutes", 60),
                    }
                    result = await call_mcp_tool("google_calendar", "find_free_slots", mcp_input, creds)
                else:
                    # create_event needs an explicit end_datetime — the tool
                    # schema only takes a duration, so compute it here.
                    from datetime import datetime, timedelta
                    start_raw = tool_input.get("start_datetime", "")
                    duration = int(tool_input.get("duration_minutes", 60))
                    try:
                        start_dt = datetime.fromisoformat(start_raw)
                        end_dt = start_dt + timedelta(minutes=duration)
                        end_iso = end_dt.isoformat()
                    except ValueError:
                        return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Invalid start_datetime: {start_raw!r}")
                    mcp_input = {
                        "title": tool_input.get("title", "Study Session"),
                        "start_datetime": start_raw,
                        "end_datetime": end_iso,
                        "description": tool_input.get("notes", ""),
                        "reminders": [{"minutes_before": 30}],
                    }
                    result = await call_mcp_tool("google_calendar", "create_event", mcp_input, creds)
            else:
                return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")

            return ToolResult(tool_name=tool_name, success=True, data=result)

        except Exception as exc:
            logger.error("education_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))
