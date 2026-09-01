"""
Healthcare Domain Agent

Handles all healthcare document actions:
  - book_appointment        → Google Calendar MCP
  - order_medicines         → Pharmacy MCP
  - create_medication_schedule → Pure Claude reasoning (no external call)
  - explain_prescription    → Pure Claude reasoning
  - medical_assistant       → Pure Claude reasoning (conversational Q&A)

All write actions (book, order) require HITL approval.
Read-only/reasoning actions skip the approval gate.
"""

from typing import Any
import structlog

from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)

# Actions that do NOT need human approval (read-only reasoning)
_NO_APPROVAL_ACTIONS = frozenset({
    "explain_prescription",
    "medical_assistant",
    "create_medication_schedule",
})

# MCP tool definitions (Anthropic tool-use schema)
_TOOLS: dict[str, list[dict]] = {

    "book_appointment": [
        {
            "name": "check_calendar_availability",
            "description": (
                "Check the user's Google Calendar for available appointment slots "
                "within the next 14 days. Returns a list of free time windows."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "preferred_date": {
                        "type": "string",
                        "description": "ISO 8601 date string e.g. '2025-09-10'. Leave null for any date.",
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Appointment duration in minutes. Default 30.",
                        "default": 30,
                    },
                },
                "required": [],
            },
        },
        {
            "name": "create_calendar_event",
            "description": "Create a medical appointment event on Google Calendar.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title, e.g. 'Follow-up with Dr. Smith'"},
                    "start_datetime": {"type": "string", "description": "ISO 8601 datetime string"},
                    "end_datetime": {"type": "string", "description": "ISO 8601 datetime string"},
                    "description": {"type": "string", "description": "Event notes / doctor info"},
                    "location": {"type": "string", "description": "Hospital or clinic name and address"},
                },
                "required": ["title", "start_datetime", "end_datetime"],
            },
        },
    ],

    "order_medicines": [
        {
            "name": "search_medicines",
            "description": (
                "Search for a medicine by name on the connected pharmacy. "
                "Returns availability and pricing."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "medicine_name": {"type": "string"},
                    "dosage": {"type": "string", "description": "e.g. '500mg'"},
                },
                "required": ["medicine_name"],
            },
        },
        {
            "name": "create_medicine_order",
            "description": (
                "Place an order for medicines on the connected pharmacy. "
                "Only call this after searching and confirming availability."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "medicine_name": {"type": "string"},
                                "dosage": {"type": "string"},
                                "quantity": {"type": "integer"},
                            },
                            "required": ["medicine_name", "quantity"],
                        },
                        "description": "List of medicines to order",
                    },
                    "delivery_address_id": {
                        "type": "string",
                        "description": "User's saved delivery address ID from pharmacy profile",
                    },
                },
                "required": ["items"],
            },
        },
    ],

    "create_medication_schedule": [],    # Pure reasoning — no tools needed
    "explain_prescription": [],          # Pure reasoning — no tools needed
    "medical_assistant": [],             # Pure reasoning — no tools needed
}


class HealthcareAgent(BaseAgent):
    DOMAIN = "healthcare"

    ACTION_PROMPTS = {
        "book_appointment": (
            "Extract the doctor name, specialty, hospital, and any follow-up instructions "
            "from the prescription. Check the user's calendar availability and create "
            "an appropriate appointment. Prefer morning slots unless the user specifies otherwise."
        ),
        "order_medicines": (
            "Extract every medicine from the prescription including name, dosage, and duration. "
            "Search for each medicine on the pharmacy, verify availability, then place a single "
            "consolidated order. Present the full order summary in your final result."
        ),
        "create_medication_schedule": (
            "From the prescription, build a detailed daily medication schedule in JSON. "
            "Include medicine name, dosage, times of day, with/without food instructions, "
            "and duration. Format it clearly for the patient."
        ),
        "explain_prescription": (
            "Provide a clear, plain-language explanation of the entire prescription. "
            "Explain what each medicine is for, how to take it, and any warnings. "
            "Use simple language the patient can understand — no medical jargon."
        ),
        "medical_assistant": (
            "Act as a knowledgeable medical document assistant. Answer the user's question "
            "about this prescription or medical document accurately and helpfully. "
            "Always recommend consulting the prescribing doctor for clinical decisions."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]

        if action_type == "book_appointment":
            doctor = ctx.get("doctor_name", "your doctor")
            hospital = ctx.get("hospital_name", "the hospital")
            steps = [
                ActionPlanStep(step_number=1, description=f"Check your calendar for available slots", requires_external_call=True, is_reversible=True, tool_name="check_calendar_availability"),
                ActionPlanStep(step_number=2, description=f"Create appointment with {doctor} at {hospital}", requires_external_call=True, is_reversible=True, tool_name="create_calendar_event"),
            ]
            external = ["google_calendar"]
            data_used = {k: ctx.get(k) for k in ["doctor_name", "hospital_name", "specialty"] if ctx.get(k)}
            risk = "low"

        elif action_type == "order_medicines":
            medicines = ctx.get("medicines", [])
            med_names = [m.get("name", "Unknown") if isinstance(m, dict) else str(m) for m in medicines[:5]]
            steps = [
                ActionPlanStep(step_number=i + 1, description=f"Search pharmacy for: {name}", requires_external_call=True, is_reversible=True, tool_name="search_medicines")
                for i, name in enumerate(med_names)
            ] + [
                ActionPlanStep(step_number=len(med_names) + 1, description="Place consolidated medicine order", requires_external_call=True, is_reversible=False, tool_name="create_medicine_order"),
            ]
            external = ["pharmacy_api"]
            data_used = {"medicines": med_names, "patient": ctx.get("patient_name")}
            risk = "medium"

        else:
            # Reasoning-only actions — no approval needed, but we still produce a plan
            steps = [
                ActionPlanStep(step_number=1, description=f"Analyse document and produce {action_type.replace('_', ' ')} output", requires_external_call=False, is_reversible=True, tool_name=None),
            ]
            external = []
            data_used = {}
            risk = "low"

        return ActionPlan(
            summary=f"Execute '{action_type}' using the extracted prescription data.",
            steps=steps,
            estimated_duration_seconds=30 if not external else 60,
            external_services=external,
            data_to_be_sent=data_used,
            risk_level=risk,
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        """Route tool calls to the correct MCP server."""
        calendar_tools = {"check_calendar_availability", "create_calendar_event"}
        pharmacy_tools = {"search_medicines", "create_medicine_order"}

        try:
            if tool_name in calendar_tools:
                # Map our tool names to actual MCP tool names
                mcp_tool = {
                    "check_calendar_availability": "find_free_slots",
                    "create_calendar_event": "create_event",
                }[tool_name]
                creds = self.user_mcp_credentials.get("google_calendar")
                result = await call_mcp_tool("google_calendar", mcp_tool, tool_input, creds)

            elif tool_name in pharmacy_tools:
                mcp_tool = {
                    "search_medicines": "search_medicines",
                    "create_medicine_order": "create_order",
                }[tool_name]
                creds = self.user_mcp_credentials.get("pharmacy_api")
                # pharmacy_api is self-hosted against our own DB (no per-user
                # credential) — the write tool needs our internal user_id to
                # scope the order, which the LLM never supplies itself.
                call_args = tool_input
                if tool_name == "create_medicine_order":
                    call_args = {**tool_input, "user_id": state["user_id"]}
                result = await call_mcp_tool("pharmacy_api", mcp_tool, call_args, creds)

            else:
                return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")

            return ToolResult(tool_name=tool_name, success=True, data=result)

        except Exception as exc:
            logger.error("healthcare_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))
