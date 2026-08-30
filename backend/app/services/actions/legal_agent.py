"""
Legal Domain Agent

Handles all legal document actions:
  - summarize_document    → Pure Claude reasoning
  - extract_key_clauses   → Pure Claude reasoning
  - track_obligations     → Google Calendar MCP (creates deadline reminders)
  - document_qa           → Pure Claude reasoning (conversational)
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)


def _party_names(raw) -> list[str]:
    """
    agent_service.py's contract_analyzer schema emits parties as
    [{"role": "...", "name": "...", "address": "..."}] — a list of dicts.
    Extract just the names for display; tolerate a flat list of strings too
    in case a different extraction pipeline ever produces that shape.
    """
    if not isinstance(raw, list):
        return []
    names = []
    for p in raw:
        if isinstance(p, dict):
            name = p.get("name")
            if name:
                names.append(str(name))
        elif isinstance(p, str) and p.strip():
            names.append(p.strip())
    return names


_NO_APPROVAL_ACTIONS = frozenset({
    "summarize_document",
    "extract_key_clauses",
    "document_qa",
})

_TOOLS: dict[str, list[dict]] = {

    "track_obligations": [
        {
            "name": "create_deadline_event",
            "description": (
                "Create a calendar event for a legal deadline or obligation. "
                "Sets a reminder 7 days and 1 day before the deadline."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "e.g. 'Contract Renewal Deadline — Vendor X'"},
                    "deadline_date": {"type": "string", "description": "ISO 8601 date string"},
                    "description": {"type": "string", "description": "Clause or obligation details"},
                    "obligation_type": {
                        "type": "string",
                        "enum": ["payment", "renewal", "notice", "performance", "reporting", "regulatory", "other"],
                    },
                },
                "required": ["title", "deadline_date", "obligation_type"],
            },
        },
        {
            "name": "list_upcoming_legal_events",
            "description": "List existing legal calendar events to avoid duplicates.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "default": 180, "description": "Look ahead window in days"},
                },
                "required": [],
            },
        },
    ],

    "summarize_document": [],
    "extract_key_clauses": [],
    "document_qa": [],
}


class LegalAgent(BaseAgent):
    DOMAIN = "legal"

    ACTION_PROMPTS = {
        "summarize_document": (
            "Produce a comprehensive yet concise executive summary of this legal document. "
            "Structure your response as JSON with these keys: "
            "'document_type' (contract/agreement/notice/policy/etc), "
            "'parties' (list of all named parties and their roles), "
            "'effective_date', 'expiry_date' (if applicable), "
            "'key_purpose' (2-3 sentence summary), "
            "'material_terms' (list of the 5-7 most important terms), "
            "'governing_law' (jurisdiction), "
            "'risk_flags' (list of potential risk areas). "
            "Be precise — this will be read by executives, not lawyers."
        ),
        "extract_key_clauses": (
            "Extract and analyse all materially significant clauses from this legal document. "
            "For each clause return: 'clause_name', 'section_reference', 'clause_text_summary', "
            "'risk_level' (low/medium/high), 'party_affected', 'action_required' (yes/no), "
            "'deadline' (if any), 'notes'. "
            "Focus on: termination, liability, indemnification, IP ownership, confidentiality, "
            "payment terms, dispute resolution, force majeure, and change-of-control clauses."
        ),
        "track_obligations": (
            "Extract every obligation, deadline, and key date from this legal document. "
            "For each, determine if it needs a calendar reminder. "
            "Create calendar events for all actionable deadlines. "
            "Return a summary of all obligations tracked."
        ),
        "document_qa": (
            "You are a precise legal document analyst. Answer the user's question about "
            "this legal document accurately, citing relevant sections. "
            "If the document does not address the question, say so clearly. "
            "Never give legal advice — provide document-based factual answers only. "
            "Always recommend consulting a qualified lawyer for legal decisions."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]
        doc_type = ctx.get("document_type", "legal document")
        # agent_service.py's contract_analyzer schema emits parties as
        # [{"role":.., "name":.., "address":..}] — a list of dicts, not strings.
        parties = _party_names(ctx.get("parties", []))
        party_str = ", ".join(parties[:2]) if parties else "identified parties"

        if action_type == "track_obligations":
            deadlines = ctx.get("key_dates", [])
            n_deadlines = len(deadlines) if deadlines else "multiple"
            steps = [
                ActionPlanStep(step_number=1, description="Check existing legal calendar events to avoid duplicates", requires_external_call=True, is_reversible=True, tool_name="list_upcoming_legal_events"),
                ActionPlanStep(step_number=2, description=f"Create calendar events for {n_deadlines} obligations/deadlines", requires_external_call=True, is_reversible=True, tool_name="create_deadline_event"),
            ]
            external = ["google_calendar"]
            data_used = {
                "document_type": doc_type,
                "parties": parties[:3],
                "key_dates_found": deadlines[:5] if deadlines else [],
            }
            risk = "low"

        else:
            steps = [
                ActionPlanStep(step_number=1, description=f"Analyse {doc_type} and produce {action_type.replace('_', ' ')}", requires_external_call=False, is_reversible=True, tool_name=None),
            ]
            external = []
            data_used = {}
            risk = "low"

        return ActionPlan(
            summary=f"Execute '{action_type}' on {doc_type} between {party_str}.",
            steps=steps,
            estimated_duration_seconds=50 if external else 20,
            external_services=external,
            data_to_be_sent=data_used,
            risk_level=risk,
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        calendar_tools = {"create_deadline_event", "list_upcoming_legal_events"}

        try:
            if tool_name in calendar_tools:
                mcp_map = {
                    "create_deadline_event": "create_event",
                    "list_upcoming_legal_events": "list_events",
                }
                if tool_name == "create_deadline_event":
                    # Build a proper calendar event from the deadline
                    deadline = tool_input.get("deadline_date", "")
                    mcp_input = {
                        "title": tool_input.get("title", "Legal Deadline"),
                        "start_datetime": f"{deadline}T09:00:00",
                        "end_datetime": f"{deadline}T09:30:00",
                        "description": tool_input.get("description", ""),
                        "reminders": [{"minutes_before": 10080}, {"minutes_before": 1440}],  # 7d + 1d
                    }
                else:
                    mcp_input = {"days_ahead": tool_input.get("days_ahead", 180)}

                creds = self.user_mcp_credentials.get("google_calendar")
                result = await call_mcp_tool("google_calendar", mcp_map[tool_name], mcp_input, creds)

            else:
                return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")

            return ToolResult(tool_name=tool_name, success=True, data=result)

        except Exception as exc:
            logger.error("legal_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))
