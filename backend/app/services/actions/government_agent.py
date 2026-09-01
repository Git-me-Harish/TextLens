"""
Government Domain Agent

Handles all government/compliance document actions:
  - summarize_filing        → Pure Claude reasoning
  - extract_obligations     → Pure Claude reasoning
  - flag_risks               → Pure Claude reasoning
  - document_qa              → Pure Claude reasoning
  - track_filing_deadlines  → Google Calendar MCP
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)

# Every action except track_filing_deadlines is read-only reasoning
_NO_APPROVAL_ACTIONS = frozenset({
    "summarize_filing",
    "extract_obligations",
    "flag_risks",
    "document_qa",
})

_TOOLS: dict[str, list[dict]] = {
    "track_filing_deadlines": [
        {
            "name": "create_deadline_event",
            "description": (
                "Create a calendar event for a filing, permit, or compliance deadline. "
                "Sets a reminder 7 days and 1 day before."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "e.g. 'Business License Renewal — City of Springfield'"},
                    "deadline_date": {"type": "string", "description": "ISO 8601 date string"},
                    "description": {"type": "string", "description": "What's due and why"},
                },
                "required": ["title", "deadline_date"],
            },
        },
        {
            "name": "list_upcoming_filing_events",
            "description": "List existing filing/compliance calendar events to avoid duplicates.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "default": 180},
                },
                "required": [],
            },
        },
    ],
}


class GovernmentAgent(BaseAgent):
    DOMAIN = "government"

    ACTION_PROMPTS = {
        "summarize_filing": (
            "Produce a plain-language summary of this government document or filing. "
            "Structure your response as JSON with: "
            "'document_type' (tax filing/permit/licence/notice/regulation/form/etc), "
            "'issuing_authority', "
            "'filing_period' or 'effective_date', "
            "'reference_number', "
            "'subject_entity' (individual or business named), "
            "'key_purpose' (2-3 sentences), "
            "'amounts_involved' (if any), "
            "'required_actions' (list of what the recipient must do), "
            "'deadlines' (list of all dates and their significance). "
            "Use simple language — assume a non-specialist reader."
        ),
        "extract_obligations": (
            "Extract every compliance obligation from this government document. "
            "For each obligation return: "
            "'obligation_id', 'description', 'responsible_party', "
            "'deadline' (ISO 8601 if specific, else 'ongoing'/'recurring'), "
            "'frequency' (one-time/monthly/quarterly/annual/ongoing), "
            "'penalty_for_non_compliance' (if stated), "
            "'status' (pending/due_soon/overdue — assess relative to today), "
            "'priority' (critical/high/medium/low). "
            "Sort by deadline ascending. Flag any already-overdue obligations as CRITICAL."
        ),
        "flag_risks": (
            "Perform a compliance risk analysis on this government document. "
            "Identify: "
            "1. Missing required information or signatures. "
            "2. Deadlines that are imminent (within 30 days) or past. "
            "3. Clauses or obligations that conflict with common regulatory requirements. "
            "4. Ambiguous language that could create compliance exposure. "
            "5. Penalties or consequences mentioned in the document. "
            "Return structured JSON: 'risk_summary', "
            "'critical_risks' (list), 'moderate_risks' (list), 'low_risks' (list), "
            "'recommended_actions' (prioritised list). "
            "Rate overall compliance risk: LOW / MEDIUM / HIGH / CRITICAL."
        ),
        "document_qa": (
            "You are a government compliance document specialist. "
            "Answer the user's question about this government document clearly and accurately. "
            "Cite the specific section or clause your answer is based on. "
            "If the document does not answer the question, say so explicitly. "
            "Always recommend consulting a qualified compliance professional or legal counsel "
            "for decisions involving regulatory submissions."
        ),
        "track_filing_deadlines": (
            "Find every deadline, expiry date, or required-action date in this document — "
            "e.g. a permit expiry, a renewal deadline, a response-due date, a filing deadline. "
            "If the document genuinely has no such dates, say so clearly and create no events — "
            "do not invent one. Otherwise, check existing filing calendar events to avoid "
            "duplicates, then create a calendar reminder for each real deadline found. "
            "Return a summary of every deadline tracked."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]
        doc_type = ctx.get("document_type", "government document")
        authority = ctx.get("issuing_authority", "the authority")

        descriptions = {
            "summarize_filing": f"Analyse {doc_type} from {authority} and produce a plain-language summary",
            "extract_obligations": f"Extract and structure all compliance obligations from {doc_type}",
            "flag_risks": f"Perform compliance risk analysis on {doc_type}",
            "document_qa": "Answer user question about the government document",
        }

        if action_type == "track_filing_deadlines":
            # permit_license has expiry_date/renewal_required; tax_form and
            # regulatory_filing don't reliably carry a future deadline the
            # same way — the prompt is written to handle "genuinely none
            # found" rather than assume one exists.
            hint = ctx.get("expiry_date") or "any deadlines found in the document"
            steps = [
                ActionPlanStep(step_number=1, description="Check existing filing calendar events to avoid duplicates", requires_external_call=True, is_reversible=True, tool_name="list_upcoming_filing_events"),
                ActionPlanStep(step_number=2, description=f"Create calendar reminders for {hint}", requires_external_call=True, is_reversible=True, tool_name="create_deadline_event"),
            ]
            external = ["google_calendar"]
            data_used = {"document_type": doc_type, "issuing_authority": authority, "expiry_date": ctx.get("expiry_date")}
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
        calendar_tools = {"create_deadline_event", "list_upcoming_filing_events"}

        try:
            if tool_name in calendar_tools:
                mcp_map = {
                    "create_deadline_event": "create_event",
                    "list_upcoming_filing_events": "list_events",
                }
                if tool_name == "create_deadline_event":
                    deadline = tool_input.get("deadline_date", "")
                    mcp_input = {
                        "title": tool_input.get("title", "Compliance Deadline"),
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
            logger.error("government_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))
