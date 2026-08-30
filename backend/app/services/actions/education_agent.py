"""
Education Domain Agent

Handles all education/knowledge document actions:
  - summarize_document      → Pure Claude reasoning
  - generate_study_material → Pure Claude reasoning
  - document_qa             → Pure Claude reasoning
  - generate_quiz           → Pure Claude reasoning
  - create_learning_plan    → Pure Claude reasoning
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult

logger = structlog.get_logger(__name__)

# All education actions are read-only reasoning — none need HITL approval
_NO_APPROVAL_ACTIONS = frozenset({
    "summarize_document",
    "generate_study_material",
    "document_qa",
    "generate_quiz",
    "create_learning_plan",
})


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
    }

    def _requires_approval(self, action_type: str) -> bool:
        # All education actions are read-only — no external write calls
        return False

    def _build_tools(self, action_type: str) -> list[dict]:
        # No external tool calls for education domain — pure Claude reasoning
        return []

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
        # Education agent has no external tool calls — this should never be reached
        return ToolResult(
            tool_name=tool_name,
            success=False,
            data=None,
            error=f"Education agent does not support external tool: {tool_name}",
        )
