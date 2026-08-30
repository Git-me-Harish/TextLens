"""
BaseAgent — the foundation every domain agent inherits.

Architecture:
  - A hand-rolled async ReAct loop (think → act → observe → repeat) driven by
    the Anthropic Messages API — no LangGraph/LangChain involved. State is a
    plain typed dict (AgentState) local to one run() or resume_after_approval()
    call; it is not persisted or checkpointed mid-loop.
  - HITL gate: when an action requires approval, run() suspends by returning
    {"status": "AWAITING_APPROVAL", "plan": ...} *before* entering the ReAct
    loop. The Celery task layer (worker/action_tasks.py) persists the plan
    and a signed approval token on the ActionRun row and ends the task there.
    A second, independent Celery task (resume_action_task) later calls
    resume_after_approval(), which builds a fresh AgentState and runs the
    ReAct loop from scratch using the persisted plan as context — there is no
    in-process resumption of a suspended coroutine or graph checkpoint.
  - Hard caps: MAX_ITERATIONS=15, MAX_TOOL_CALLS=30, TIMEOUT=300s
  - Every LLM call and tool call emits an AgentTrace row to DB
  - All tool calls validated against the MCP server's allowlist before execution

Subclasses override:
  - DOMAIN                  : str
  - ACTION_PROMPTS           : dict[action_type, system_prompt]
  - _build_tools()           : return list of Anthropic tool-use JSON schema defs
  - _plan()                  : produce a structured ActionPlan
  - _requires_approval()     : bool — whether this action needs HITL

State machine managed externally by action_service.py.
"""

import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, TypedDict

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.action_models import ActionRun, AgentTrace
from app.schemas.action_schemas import ActionPlan, ActionPlanStep

logger = structlog.get_logger(__name__)

# Hard safety caps — never negotiable
MAX_ITERATIONS = 15
MAX_TOOL_CALLS = 30
PREVIEW_MAX_CHARS = 500


# ─────────────────────────────────────────────────────────────────────────────
# Typed agent state
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    action_run_id: str
    user_id: str
    action_type: str
    domain: str
    document_context: dict          # structured extraction from AgentRun
    user_context: str | None        # optional free-text from user
    plan: ActionPlan | None
    messages: list[dict]            # full conversation history for ReAct loop
    tool_calls_made: int
    iterations: int
    awaiting_approval: bool
    approval_granted: bool
    result: dict | None
    error: str | None
    trace_id: str
    span_counter: int


# ─────────────────────────────────────────────────────────────────────────────
# Tool result container
# ─────────────────────────────────────────────────────────────────────────────

class ToolResult:
    def __init__(self, tool_name: str, success: bool, data: Any, error: str | None = None):
        self.tool_name = tool_name
        self.success = success
        self.data = data
        self.error = error


# ─────────────────────────────────────────────────────────────────────────────
# Base agent
# ─────────────────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    DOMAIN: str = ""
    ACTION_PROMPTS: dict[str, str] = {}

    def __init__(
        self,
        db: AsyncSession,
        anthropic_client: AsyncAnthropic,
        user_mcp_credentials: dict[str, dict],   # service_name → decrypted creds
    ):
        self.db = db
        self.client = anthropic_client
        self.user_mcp_credentials = user_mcp_credentials

    # ── Subclass interface ────────────────────────────────────────────────

    @abstractmethod
    async def _plan(self, state: AgentState) -> ActionPlan:
        """
        Produce a structured ActionPlan from the document context.
        Called before the HITL gate.
        """

    @abstractmethod
    def _build_tools(self, action_type: str) -> list[dict]:
        """
        Return the Anthropic tool definitions (JSON schema) for the given action_type.
        Must only reference tools in the MCP server allowlists.
        """

    @abstractmethod
    async def _execute_tool(
        self, tool_name: str, tool_input: dict, state: AgentState
    ) -> ToolResult:
        """
        Execute one tool call against the appropriate MCP server.
        Must validate tool_name against allowlist before calling registry.
        """

    def _requires_approval(self, action_type: str) -> bool:
        """
        Returns True if this action requires human confirmation before execution.
        Subclasses can override per action_type. Default: True for all.
        """
        return True

    # ── Trace emission ────────────────────────────────────────────────────

    async def _emit_trace(
        self,
        state: AgentState,
        event_type: str,
        tool_name: str | None = None,
        input_preview: str | None = None,
        output_preview: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        success: bool | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Append an immutable trace event to the DB."""
        span_id = f"{state['trace_id']}-{state['span_counter']:04d}"
        state["span_counter"] += 1

        trace = AgentTrace(
            action_run_id=state["action_run_id"],
            trace_id=state["trace_id"],
            span_id=span_id,
            event_type=event_type,
            tool_name=tool_name,
            input_preview=input_preview[:PREVIEW_MAX_CHARS] if input_preview else None,
            output_preview=output_preview[:PREVIEW_MAX_CHARS] if output_preview else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            success=success,
            error_msg=error_msg,
        )
        self.db.add(trace)
        try:
            await self.db.commit()
        except Exception as exc:
            logger.warning("trace.commit_failed", error=str(exc))
            await self.db.rollback()

    # ── LLM call helper ───────────────────────────────────────────────────

    async def _llm_call(
        self,
        state: AgentState,
        system: str,
        tools: list[dict] | None = None,
    ) -> Any:
        """
        Single Anthropic API call with tracing.
        Uses settings.AGENT_MODEL — change AGENT_MODEL in .env if needed.
        """
        start = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": settings.AGENT_MODEL,
                "max_tokens": 4096,
                "system": system,
                "messages": state["messages"],
            }
            if tools:
                kwargs["tools"] = tools

            response = await self.client.messages.create(**kwargs)
            latency_ms = int((time.monotonic() - start) * 1000)

            await self._emit_trace(
                state,
                event_type="llm_call",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency_ms,
                success=True,
            )
            return response

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            await self._emit_trace(
                state,
                event_type="llm_call",
                latency_ms=latency_ms,
                success=False,
                error_msg=str(exc)[:500],
            )
            raise

    # ── Core execution loop ───────────────────────────────────────────────

    async def run(
        self,
        action_run: ActionRun,
        document_context: dict,
        on_plan_ready: Any = None,   # async callable(plan, approval_token)
        on_tool_called: Any = None,  # async callable(tool_name, success, latency_ms)
    ) -> dict:
        """
        Full agent execution:
          1. Build initial state
          2. Produce plan
          3. HITL gate (if required) — suspend here until approval
          4. ReAct execution loop
          5. Return structured result

        on_plan_ready and on_tool_called are async SSE callbacks injected
        by the Celery task layer to push real-time events to the frontend.
        """
        state: AgentState = {
            "action_run_id": action_run.id,
            "user_id": action_run.user_id,
            "action_type": action_run.action_type,
            "domain": action_run.domain,
            "document_context": document_context,
            "user_context": action_run.user_context,
            "plan": None,
            "messages": [],
            "tool_calls_made": 0,
            "iterations": 0,
            "awaiting_approval": False,
            "approval_granted": False,
            "result": None,
            "error": None,
            "trace_id": str(uuid.uuid4()),
            "span_counter": 0,
        }

        log = logger.bind(
            action_run_id=action_run.id,
            action_type=action_run.action_type,
            trace_id=state["trace_id"],
        )

        # ── Step 1: Plan ─────────────────────────────────────────────────
        log.info("agent.planning")
        try:
            plan = await self._plan(state)
            state["plan"] = plan
        except Exception as exc:
            log.error("agent.plan_failed", error=str(exc))
            raise RuntimeError(f"Planning failed: {exc}") from exc

        # ── Step 2: HITL gate ─────────────────────────────────────────────
        requires_approval = self._requires_approval(action_run.action_type)
        if requires_approval and on_plan_ready:
            log.info("agent.awaiting_approval")
            await self._emit_trace(state, event_type="hitl_gate", success=True)
            # Suspend — action_service will resume us after user approves
            # on_plan_ready updates the DB and emits the SSE event
            await on_plan_ready(plan)
            # This coroutine is now suspended until action_service resumes it
            # (In Celery: the task finishes here; a new task picks up after approval)
            return {"status": "AWAITING_APPROVAL", "plan": plan.model_dump()}

        # ── Step 3: Execute ───────────────────────────────────────────────
        result = await self._execute(state, log, on_tool_called)
        action_run.total_llm_calls = state["iterations"]
        action_run.total_tool_calls = state["tool_calls_made"]
        return result

    async def resume_after_approval(
        self,
        action_run: ActionRun,
        document_context: dict,
        saved_plan: dict,
        on_tool_called: Any = None,
    ) -> dict:
        """
        Resume execution after human approval.
        Called by a fresh Celery task that picks up after the HITL gate.
        """
        state: AgentState = {
            "action_run_id": action_run.id,
            "user_id": action_run.user_id,
            "action_type": action_run.action_type,
            "domain": action_run.domain,
            "document_context": document_context,
            "user_context": action_run.user_context,
            "plan": ActionPlan(**saved_plan),
            "messages": [],
            "tool_calls_made": 0,
            "iterations": 0,
            "awaiting_approval": False,
            "approval_granted": True,
            "result": None,
            "error": None,
            "trace_id": str(uuid.uuid4()),
            "span_counter": 0,
        }
        log = logger.bind(
            action_run_id=action_run.id,
            action_type=action_run.action_type,
            trace_id=state["trace_id"],
            resumed=True,
        )
        log.info("agent.resuming_after_approval")
        result = await self._execute(state, log, on_tool_called)
        # Resume runs in a fresh Celery task/state — the original run() returned
        # before any execution happened (it suspended at the HITL gate), so these
        # counts are not double-counted against a prior execution.
        action_run.total_llm_calls = state["iterations"]
        action_run.total_tool_calls = state["tool_calls_made"]
        return result

    async def _execute(self, state: AgentState, log: Any, on_tool_called: Any) -> dict:
        """
        Core ReAct loop: think → act → observe → repeat until done or cap hit.
        """
        tools = self._build_tools(state["action_type"])
        system_prompt = self._build_system_prompt(state)

        # Prime the conversation with the document context
        state["messages"].append({
            "role": "user",
            "content": self._build_initial_user_message(state),
        })

        while state["iterations"] < MAX_ITERATIONS:
            state["iterations"] += 1
            log.info("agent.iteration", iteration=state["iterations"])

            # ── LLM thinks ───────────────────────────────────────────────
            response = await self._llm_call(state, system=system_prompt, tools=tools)

            # Append assistant turn
            state["messages"].append({
                "role": "assistant",
                "content": response.content,
            })

            # ── Check stop condition ──────────────────────────────────────
            if response.stop_reason == "end_turn":
                # Extract final result from the last text block
                final_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                state["result"] = self._parse_final_result(final_text, state)
                log.info("agent.completed", iterations=state["iterations"])
                return state["result"]

            # ── Process tool calls ────────────────────────────────────────
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                # Model returned neither end_turn nor tool_use — treat as done
                state["result"] = {"summary": "Action completed.", "details": {}}
                return state["result"]

            tool_results = []
            for block in tool_use_blocks:
                if state["tool_calls_made"] >= MAX_TOOL_CALLS:
                    raise RuntimeError(
                        f"Agent exceeded MAX_TOOL_CALLS={MAX_TOOL_CALLS}. "
                        "Stopping to prevent runaway execution."
                    )

                start = time.monotonic()
                tool_result = await self._execute_tool(block.name, block.input, state)
                latency_ms = int((time.monotonic() - start) * 1000)
                state["tool_calls_made"] += 1

                await self._emit_trace(
                    state,
                    event_type="tool_call",
                    tool_name=block.name,
                    input_preview=str(block.input),
                    output_preview=str(tool_result.data)[:PREVIEW_MAX_CHARS],
                    latency_ms=latency_ms,
                    success=tool_result.success,
                    error_msg=tool_result.error,
                )

                if on_tool_called:
                    await on_tool_called(block.name, tool_result.success, latency_ms)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": (
                        str(tool_result.data)
                        if tool_result.success
                        else f"ERROR: {tool_result.error}"
                    ),
                })

            # Append tool results as user turn
            state["messages"].append({
                "role": "user",
                "content": tool_results,
            })

        raise RuntimeError(
            f"Agent exceeded MAX_ITERATIONS={MAX_ITERATIONS} without completing. "
            "This may indicate a planning or tool error."
        )

    # ── Prompt builders ───────────────────────────────────────────────────

    def _build_system_prompt(self, state: AgentState) -> str:
        base = self.ACTION_PROMPTS.get(state["action_type"], "")
        return f"""You are an expert {self.DOMAIN} document intelligence agent.
Your task is to execute the action: {state['action_type']}

{base}

CRITICAL RULES:
1. Only use the tools provided to you. Never hallucinate tool results.
2. Extract all required information from the document context provided.
3. If a tool fails, report the error clearly — do not retry more than once.
4. When you have completed the action, return a structured JSON result with keys:
   "summary" (str), "details" (dict), "next_steps" (list[str]).
5. Never include sensitive credentials or PII in your final output.
6. Be concise in tool arguments — include only what the tool needs.

Document domain: {state['domain']}
Action type: {state['action_type']}
"""

    def _build_initial_user_message(self, state: AgentState) -> str:
        ctx_lines = []
        for k, v in state["document_context"].items():
            ctx_lines.append(f"  {k}: {v}")
        context_str = "\n".join(ctx_lines)

        user_ctx = (
            f"\nAdditional user instructions: {state['user_context']}"
            if state["user_context"]
            else ""
        )
        plan_str = (
            f"\nApproved execution plan:\n{state['plan'].model_dump_json(indent=2)}"
            if state["plan"]
            else ""
        )

        return (
            f"Please execute the '{state['action_type']}' action "
            f"using the following document intelligence output:\n\n"
            f"EXTRACTED DOCUMENT DATA:\n{context_str}"
            f"{user_ctx}{plan_str}\n\n"
            f"Begin execution now. Use your tools as needed."
        )

    def _parse_final_result(self, text: str, state: AgentState) -> dict:
        """
        Parse the LLM's final text response into a structured result dict.
        Falls back gracefully if JSON is not well-formed.
        """
        import json
        import re

        # Try to find a JSON block in the response
        json_match = re.search(r"\{[\s\S]+\}", text)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if "summary" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass

        # Fallback: wrap the raw text
        return {
            "summary": text[:500] if text else "Action completed.",
            "details": {
                "action_type": state["action_type"],
                "tool_calls_made": state["tool_calls_made"],
                "iterations": state["iterations"],
            },
            "next_steps": [],
        }
