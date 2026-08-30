"""
Government Domain Agent

Handles all government/compliance document actions:
  - summarize_filing      → Pure Claude reasoning
  - extract_obligations   → Pure Claude reasoning
  - flag_risks            → Pure Claude reasoning
  - document_qa           → Pure Claude reasoning
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult

logger = structlog.get_logger(__name__)

# All government actions are read-only reasoning — none need HITL approval
_NO_APPROVAL_ACTIONS = frozenset({
    "summarize_filing",
    "extract_obligations",
    "flag_risks",
    "document_qa",
})


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
    }

    def _requires_approval(self, action_type: str) -> bool:
        # All government actions are read-only — no external write calls
        return False

    def _build_tools(self, action_type: str) -> list[dict]:
        # No external tool calls for government domain — pure Claude reasoning
        return []

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

        return ActionPlan(
            summary=f"Execute '{action_type}' on {doc_type}.",
            steps=[
                ActionPlanStep(
                    step_number=1,
                    description=descriptions.get(action_type, f"Process {action_type}"),
                    requires_external_call=False,
                    is_reversible=True,
                    tool_name=None,
                )
            ],
            estimated_duration_seconds=15,
            external_services=[],
            data_to_be_sent={},
            risk_level="low",
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        # Government agent has no external tool calls — this should never be reached
        return ToolResult(
            tool_name=tool_name,
            success=False,
            data=None,
            error=f"Government agent does not support external tool: {tool_name}",
        )
