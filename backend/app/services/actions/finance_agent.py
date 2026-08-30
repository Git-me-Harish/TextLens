"""
Finance Domain Agent

Handles all finance document actions:
  - create_expense_entry      → Accounting MCP
  - validate_invoice          → Pure Claude reasoning
  - generate_financial_report → Pure Claude reasoning
  - send_payment_reminder     → Email MCP
"""

import structlog
from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)

_NO_APPROVAL_ACTIONS = frozenset({"validate_invoice", "generate_financial_report"})

_TOOLS: dict[str, list[dict]] = {

    "create_expense_entry": [
        {
            "name": "list_accounting_accounts",
            "description": "List available expense accounts in the connected accounting system.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "account_type": {
                        "type": "string",
                        "enum": ["expense", "liability", "asset"],
                        "default": "expense",
                    }
                },
                "required": [],
            },
        },
        {
            "name": "create_expense",
            "description": "Create an expense entry in the connected accounting system.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "vendor_name": {"type": "string"},
                    "amount": {"type": "number", "description": "Total amount excluding tax"},
                    "tax_amount": {"type": "number", "description": "Tax amount, 0 if none"},
                    "currency": {"type": "string", "description": "ISO 4217 code e.g. USD, INR, EUR"},
                    "invoice_date": {"type": "string", "description": "ISO 8601 date string"},
                    "invoice_number": {"type": "string"},
                    "account_id": {"type": "string", "description": "Account ID from list_accounting_accounts"},
                    "description": {"type": "string"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit_price": {"type": "number"},
                                "total": {"type": "number"},
                            },
                        },
                    },
                },
                "required": ["vendor_name", "amount", "currency", "invoice_date"],
            },
        },
    ],

    "send_payment_reminder": [
        {
            "name": "send_payment_reminder_email",
            "description": "Send a payment reminder email for an overdue invoice.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Recipient email address"},
                    "to_name": {"type": "string", "description": "Recipient name or company"},
                    "invoice_number": {"type": "string"},
                    "amount_due": {"type": "number"},
                    "currency": {"type": "string"},
                    "due_date": {"type": "string", "description": "Original due date ISO 8601"},
                    "days_overdue": {"type": "integer"},
                    "sender_name": {"type": "string", "description": "Name of the sending company/person"},
                },
                "required": ["to_email", "invoice_number", "amount_due", "currency"],
            },
        },
    ],

    "validate_invoice": [],
    "generate_financial_report": [],
}


class FinanceAgent(BaseAgent):
    DOMAIN = "finance"

    ACTION_PROMPTS = {
        "create_expense_entry": (
            "Extract all financial details from the invoice: vendor, amount, tax, currency, "
            "invoice number, date, and line items. First list the available accounts, then "
            "select the most appropriate expense account, and create the entry. "
            "Return the created entry ID and summary."
        ),
        "validate_invoice": (
            "Carefully validate this invoice for completeness and anomalies. Check: "
            "1) All required fields are present (vendor, date, number, amounts, GST/tax). "
            "2) Math is correct — line items sum to subtotal, tax is calculated correctly, total matches. "
            "3) Invoice number format and date are valid. "
            "4) Flag any suspicious patterns (round numbers, missing details, unusual line items). "
            "Return a structured validation report with PASS/FAIL status and specific findings."
        ),
        "generate_financial_report": (
            "From the extracted financial document data, generate a concise executive-level "
            "financial report. Include: key figures, period, trends if apparent, and a "
            "3-5 point summary of financial health indicators. Format as structured JSON."
        ),
        "send_payment_reminder": (
            "Extract the invoice details from the document. The recipient's email "
            "address is provided separately as 'recipient_email' in the approved "
            "plan's data — use that exact address for to_email, do not invent one. "
            "Calculate days overdue if possible. Draft and send a professional, "
            "firm-but-courteous payment reminder email. Return confirmation with email details."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]
        vendor = ctx.get("vendor_name", "the vendor")
        amount = ctx.get("total_amount", "unknown amount")
        currency = ctx.get("currency", "")

        if action_type == "create_expense_entry":
            steps = [
                ActionPlanStep(step_number=1, description="List available expense accounts", requires_external_call=True, is_reversible=True, tool_name="list_accounting_accounts"),
                ActionPlanStep(step_number=2, description=f"Create expense entry: {vendor} — {currency} {amount}", requires_external_call=True, is_reversible=False, tool_name="create_expense"),
            ]
            external = ["accounting_api"]
            # invoice_processor's real field is 'tax', not 'tax_amount'
            data_used = {k: ctx.get(k) for k in ["vendor_name", "total_amount", "currency", "invoice_number", "invoice_date", "tax"] if ctx.get(k)}
            risk = "medium"

        elif action_type == "send_payment_reminder":
            # No finance extraction schema captures a customer/bill-to name or
            # email — invoice_processor only captures the vendor (issuer) side,
            # since it's designed for invoices the user received, not ones they
            # sent. Rather than silently defaulting to a fake "the customer"
            # placeholder with no real address, require it explicitly.
            recipient = ctx.get("customer_name") or ctx.get("bill_to_name") or ctx.get("recipient_name")
            recipient_email = ctx.get("customer_email") or (state.get("user_context") or "").strip()
            if not recipient_email:
                raise ValueError(
                    "send_payment_reminder needs the recipient's email address — "
                    "the source document doesn't contain one. Re-run this action "
                    "with the recipient's email in the instructions box."
                )
            recipient = recipient or "the customer"
            steps = [
                ActionPlanStep(step_number=1, description=f"Send payment reminder to {recipient}", requires_external_call=True, is_reversible=False, tool_name="send_payment_reminder_email"),
            ]
            external = ["email_api"]
            data_used = {k: ctx.get(k) for k in ["invoice_number", "total_amount", "due_date"] if ctx.get(k)}
            data_used["recipient_email"] = recipient_email
            risk = "low"

        else:
            steps = [
                ActionPlanStep(step_number=1, description=f"Analyse document and produce {action_type.replace('_', ' ')}", requires_external_call=False, is_reversible=True, tool_name=None),
            ]
            external = []
            data_used = {}
            risk = "low"

        return ActionPlan(
            summary=f"Execute '{action_type}' on {vendor} document.",
            steps=steps,
            estimated_duration_seconds=40 if external else 15,
            external_services=external,
            data_to_be_sent=data_used,
            risk_level=risk,
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        accounting_tools = {"list_accounting_accounts", "create_expense"}
        email_tools = {"send_payment_reminder_email"}

        try:
            if tool_name in accounting_tools:
                mcp_map = {
                    "list_accounting_accounts": "get_account_list",
                    "create_expense": "create_expense",
                }
                creds = self.user_mcp_credentials.get("accounting_api")
                result = await call_mcp_tool("accounting_api", mcp_map[tool_name], tool_input, creds)

            elif tool_name in email_tools:
                creds = self.user_mcp_credentials.get("email_api")
                result = await call_mcp_tool("email_api", "send_email", {
                    "to": tool_input["to_email"],
                    "subject": f"Payment Reminder — Invoice {tool_input.get('invoice_number', '')}",
                    "body": _build_reminder_body(tool_input),
                }, creds)

            else:
                return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")

            return ToolResult(tool_name=tool_name, success=True, data=result)

        except Exception as exc:
            logger.error("finance_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))


def _build_reminder_body(inp: dict) -> str:
    days_note = f" ({inp.get('days_overdue', 0)} days overdue)" if inp.get("days_overdue") else ""
    return (
        f"Dear {inp.get('to_name', 'Sir/Madam')},\n\n"
        f"This is a friendly reminder that Invoice {inp.get('invoice_number', '')} "
        f"for {inp.get('currency', '')} {inp.get('amount_due', '')} "
        f"was due on {inp.get('due_date', 'the due date')}{days_note}.\n\n"
        f"Please arrange payment at your earliest convenience.\n\n"
        f"Regards,\n{inp.get('sender_name', 'Finance Team')}"
    )
