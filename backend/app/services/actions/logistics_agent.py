"""
Logistics / Supply Chain Domain Agent

Handles all logistics document actions:
  - track_shipment      → Google Calendar MCP (delivery date reminder)
  - notify_consignee    → Email MCP (shipment + tracking details)
  - record_po_expense   → Accounting MCP (PO booked as a committed expense)
  - summarize_shipment  → Pure Claude reasoning
  - flag_customs_risks  → Pure Claude reasoning

Sources its document_context from agent_service.py's logistics pipelines:
waybill_parser, purchase_order, customs_declaration, packing_list. Those
four emit meaningfully different shapes for the same conceptual fields
(a party is a {name, address, contact} dict on waybill/purchase_order but
a plain string on packing_list/customs_declaration; the reference number
and the relevant date each live under a different key per pipeline) — the
helpers below normalise across all four rather than assuming one shape.
"""

import structlog

from app.schemas.action_schemas import ActionPlan, ActionPlanStep
from app.services.actions.base_agent import AgentState, BaseAgent, ToolResult
from app.services.mcp.registry import call_mcp_tool

logger = structlog.get_logger(__name__)

_NO_APPROVAL_ACTIONS = frozenset({"summarize_shipment", "flag_customs_risks"})


def _party(ctx: dict, *keys: str) -> dict:
    """
    Pull the first present party out of the context as {name, contact}.

    waybill_parser / purchase_order emit {"name":.., "address":.., "contact":..};
    packing_list / customs_declaration emit a bare string. Tolerate both.
    """
    for key in keys:
        raw = ctx.get(key)
        if isinstance(raw, dict) and raw.get("name"):
            return {"name": str(raw["name"]), "contact": str(raw.get("contact") or "")}
        if isinstance(raw, str) and raw.strip():
            return {"name": raw.strip(), "contact": ""}
    return {"name": "", "contact": ""}


def _reference(ctx: dict) -> str:
    """The document's own reference number, whichever pipeline produced it."""
    for key in ("tracking_number", "po_number", "declaration_number", "packing_list_number"):
        value = ctx.get(key)
        if value:
            return str(value)
    return ""


def _delivery_date(ctx: dict) -> str:
    """The date this document implies someone should be waiting on."""
    for key in ("expected_delivery", "required_delivery_date", "ship_date", "declaration_date"):
        value = ctx.get(key)
        if value:
            return str(value)
    return ""


def _looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1]


_TOOLS: dict[str, list[dict]] = {

    "track_shipment": [
        {
            "name": "create_shipment_event",
            "description": (
                "Create a calendar reminder for a shipment's expected delivery or "
                "required delivery date. Sets reminders 1 day before and on the day."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "e.g. 'Delivery due — PO-4471 from Acme Supplies'"},
                    "delivery_date": {"type": "string", "description": "ISO 8601 date string"},
                    "description": {"type": "string", "description": "Carrier, tracking number, origin/destination, package summary"},
                },
                "required": ["title", "delivery_date"],
            },
        },
        {
            "name": "list_upcoming_shipments",
            "description": "List existing shipment calendar events to avoid creating a duplicate.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "default": 90},
                },
                "required": [],
            },
        },
    ],

    "notify_consignee": [
        {
            "name": "send_shipment_notification",
            "description": (
                "Email the consignee (or buyer) the shipment details and tracking "
                "reference so they know what is arriving and when."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "Consignee/buyer email address"},
                    "recipient_name": {"type": "string"},
                    "reference": {"type": "string", "description": "Tracking number, PO number, or declaration number"},
                    "carrier": {"type": "string"},
                    "expected_delivery": {"type": "string", "description": "ISO 8601 date, if known"},
                    "contents_summary": {"type": "string", "description": "One-line summary of what is being shipped"},
                },
                "required": ["to_email", "reference"],
            },
        },
    ],

    "record_po_expense": [
        {
            "name": "list_accounting_accounts",
            "description": "List available expense accounts to book the purchase order against.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "account_type": {"type": "string", "enum": ["expense", "liability", "asset"], "default": "expense"},
                },
                "required": [],
            },
        },
        {
            "name": "record_purchase_order",
            "description": (
                "Book the purchase order into the accounting ledger as a committed "
                "expense. Only call this after confirming the account to use."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "supplier_name": {"type": "string"},
                    "amount": {"type": "number", "description": "PO total excluding tax"},
                    "tax_amount": {"type": "number", "description": "Tax amount, 0 if none"},
                    "currency": {"type": "string", "description": "ISO 4217 code e.g. USD, INR, EUR"},
                    "po_date": {"type": "string", "description": "ISO 8601 date string"},
                    "po_number": {"type": "string"},
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
                "required": ["supplier_name", "amount", "currency", "po_date"],
            },
        },
    ],

    "summarize_shipment": [],
    "flag_customs_risks": [],
}


class LogisticsAgent(BaseAgent):
    DOMAIN = "logistics"

    ACTION_PROMPTS = {
        "track_shipment": (
            "Find the delivery date this document implies someone is waiting on — the "
            "expected delivery on a waybill, the required delivery date on a purchase "
            "order, or the ship date on a packing list. If the document has no such date, "
            "say so clearly and create no event — do not invent one. Otherwise check for "
            "an existing reminder first, then create one calendar event with the carrier, "
            "tracking reference, route, and package summary in the description. "
            "Return JSON with: 'reference', 'delivery_date', 'carrier', 'event_created' (boolean)."
        ),
        "notify_consignee": (
            "Draft and send a clear, professional shipment notification to the consignee "
            "(or the buyer, on a purchase order). Use the recipient email from the document "
            "if it has one, otherwise use the address given in the user's instructions. "
            "Include the tracking/PO reference, the carrier, the expected delivery date, and "
            "a one-line summary of the contents. Keep it short — this is an operational "
            "notice, not marketing. Return JSON with: 'to_email', 'reference', 'sent' (boolean)."
        ),
        "record_po_expense": (
            "Book this purchase order into the accounting ledger as a committed expense. "
            "First list the available expense accounts and choose the most appropriate one "
            "for what is being purchased. Then record the purchase order using the supplier, "
            "PO total, tax, currency, PO date, and line items from the document. If the "
            "document is not a purchase order (no supplier or total), say so clearly and "
            "record nothing. Return JSON with: 'po_number', 'supplier', 'amount', 'currency', "
            "'account_used', 'recorded' (boolean)."
        ),
        "summarize_shipment": (
            "Produce a clear operational summary of this logistics document. Return JSON with: "
            "'document_type', 'reference' (tracking/PO/declaration number), "
            "'parties' ({shipper_or_supplier, consignee_or_buyer}), "
            "'route' ({origin, destination}), 'key_dates' (list of {label, date}), "
            "'contents_summary' (what is being moved, in one or two sentences), "
            "'totals' ({weight_kg, packages, declared_value, currency}), "
            "'watch_items' (anything an ops person should chase — missing dates, "
            "incomplete addresses, unusual incoterms). Write for a warehouse or ops "
            "coordinator, not a compliance officer."
        ),
        "flag_customs_risks": (
            "Perform a customs and trade-compliance risk review of this document. Assess: "
            "1. HS code plausibility against the goods described — flag mismatches or missing codes. "
            "2. Declared value against quantity and goods type — flag possible undervaluation. "
            "3. Missing documentation implied by the goods (certificates of origin, licences, permits). "
            "4. Incoterms consistency with who is named as importer/exporter. "
            "5. Country-of-origin and destination pairings that commonly attract scrutiny. "
            "Return JSON with: 'risk_summary', 'critical_risks' (list), 'moderate_risks' (list), "
            "'low_risks' (list), 'recommended_actions' (prioritised list), "
            "'overall_risk' (LOW/MEDIUM/HIGH/CRITICAL). "
            "Base every finding on what the document actually says — never assert a violation "
            "you cannot point to. Recommend a licensed customs broker for filing decisions."
        ),
    }

    def _requires_approval(self, action_type: str) -> bool:
        return action_type not in _NO_APPROVAL_ACTIONS

    def _build_tools(self, action_type: str) -> list[dict]:
        return _TOOLS.get(action_type, [])

    async def _plan(self, state: AgentState) -> ActionPlan:
        ctx = state["document_context"]
        action_type = state["action_type"]
        doc_type = ctx.get("document_type") or ctx.get("declaration_type") or "logistics document"
        reference = _reference(ctx)
        ref_label = reference or "this shipment"
        consignee = _party(ctx, "consignee", "buyer", "importer")
        supplier = _party(ctx, "shipper", "supplier", "exporter")

        if action_type == "track_shipment":
            delivery_date = _delivery_date(ctx)
            steps = [
                ActionPlanStep(step_number=1, description="Check existing shipment reminders to avoid duplicates", requires_external_call=True, is_reversible=True, tool_name="list_upcoming_shipments"),
                ActionPlanStep(step_number=2, description=f"Create delivery reminder for {ref_label}" + (f" on {delivery_date}" if delivery_date else ""), requires_external_call=True, is_reversible=True, tool_name="create_shipment_event"),
            ]
            external = ["google_calendar"]
            data_used = {"reference": reference, "delivery_date": delivery_date, "carrier": ctx.get("carrier")}
            risk = "low"

        elif action_type == "notify_consignee":
            # Fail fast before burning an LLM call if there's no address to send to
            # anywhere — the document's contact field is often a phone number, and
            # the instructions box is the documented way to supply one.
            instructions = (state.get("user_context") or "").strip()
            if not _looks_like_email(consignee["contact"]) and not _looks_like_email(instructions):
                raise ValueError(
                    "notify_consignee needs an email address. This document doesn't "
                    "contain one for the consignee — re-run the action with the "
                    "recipient's email in the instructions box."
                )
            steps = [
                ActionPlanStep(step_number=1, description=f"Draft shipment notification for {consignee['name'] or 'the consignee'}", requires_external_call=False, is_reversible=True, tool_name=None),
                ActionPlanStep(step_number=2, description=f"Email shipment details for {ref_label}", requires_external_call=True, is_reversible=False, tool_name="send_shipment_notification"),
            ]
            external = ["email_api"]
            data_used = {
                "recipient": consignee["name"],
                "reference": reference,
                "expected_delivery": _delivery_date(ctx),
            }
            risk = "medium"

        elif action_type == "record_po_expense":
            total = ctx.get("total")
            currency = ctx.get("currency") or ""
            steps = [
                ActionPlanStep(step_number=1, description="List available expense accounts", requires_external_call=True, is_reversible=True, tool_name="list_accounting_accounts"),
                ActionPlanStep(step_number=2, description=f"Book PO {reference or ''} from {supplier['name'] or 'supplier'} — {currency} {total}".strip(), requires_external_call=True, is_reversible=False, tool_name="record_purchase_order"),
            ]
            external = ["accounting_api"]
            data_used = {
                "po_number": reference,
                "supplier": supplier["name"],
                "total": total,
                "currency": currency,
            }
            risk = "medium"

        else:
            steps = [
                ActionPlanStep(step_number=1, description=f"Analyse {doc_type} and produce {action_type.replace('_', ' ')}", requires_external_call=False, is_reversible=True, tool_name=None),
            ]
            external = []
            data_used = {}
            risk = "low"

        return ActionPlan(
            summary=f"Execute '{action_type}' on {doc_type} ({ref_label}).",
            steps=steps,
            estimated_duration_seconds=45 if external else 20,
            external_services=external,
            data_to_be_sent=data_used,
            risk_level=risk,
        )

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: AgentState) -> ToolResult:
        calendar_tools = {"create_shipment_event", "list_upcoming_shipments"}
        email_tools = {"send_shipment_notification"}
        accounting_tools = {"list_accounting_accounts", "record_purchase_order"}

        try:
            if tool_name in calendar_tools:
                creds = self.user_mcp_credentials.get("google_calendar")
                if tool_name == "create_shipment_event":
                    delivery_date = tool_input.get("delivery_date", "")
                    mcp_input = {
                        "title": tool_input.get("title", "Shipment delivery"),
                        "start_datetime": f"{delivery_date}T09:00:00",
                        "end_datetime": f"{delivery_date}T09:30:00",
                        "description": tool_input.get("description", ""),
                        "reminders": [{"minutes_before": 1440}, {"minutes_before": 60}],  # 1d + 1h
                    }
                    result = await call_mcp_tool("google_calendar", "create_event", mcp_input, creds)
                else:
                    result = await call_mcp_tool(
                        "google_calendar", "list_events",
                        {"days_ahead": tool_input.get("days_ahead", 90)}, creds,
                    )

            elif tool_name in email_tools:
                creds = self.user_mcp_credentials.get("email_api")
                reference = tool_input.get("reference", "")
                recipient = tool_input.get("recipient_name") or "there"
                carrier = tool_input.get("carrier") or "the carrier"
                eta = tool_input.get("expected_delivery")
                contents = tool_input.get("contents_summary") or "your shipment"
                body_lines = [
                    f"Hi {recipient},",
                    "",
                    f"Your shipment ({reference}) is on its way via {carrier}.",
                    f"Contents: {contents}",
                ]
                if eta:
                    body_lines.append(f"Expected delivery: {eta}")
                body_lines += ["", "You'll receive a further update if anything changes."]
                result = await call_mcp_tool("email_api", "send_email", {
                    "to": tool_input["to_email"],
                    "subject": f"Shipment update — {reference}" if reference else "Shipment update",
                    "body": "\n".join(body_lines),
                }, creds)

            elif tool_name in accounting_tools:
                creds = self.user_mcp_credentials.get("accounting_api")
                if tool_name == "list_accounting_accounts":
                    result = await call_mcp_tool(
                        "accounting_api", "get_account_list",
                        {"account_type": tool_input.get("account_type", "expense")}, creds,
                    )
                else:
                    # accounting_api is self-hosted against our own DB (no per-user
                    # credential) — the write tool needs our internal user_id to
                    # scope the ledger entry, which the LLM never supplies itself.
                    mcp_input = {
                        "vendor_name": tool_input.get("supplier_name"),
                        "amount": tool_input.get("amount"),
                        "tax_amount": tool_input.get("tax_amount") or 0,
                        "currency": tool_input.get("currency"),
                        "invoice_date": tool_input.get("po_date"),
                        "invoice_number": tool_input.get("po_number"),
                        "account_id": tool_input.get("account_id"),
                        "description": tool_input.get("description") or f"Purchase order {tool_input.get('po_number', '')}".strip(),
                        "line_items": tool_input.get("line_items"),
                        "user_id": state["user_id"],
                    }
                    result = await call_mcp_tool("accounting_api", "create_expense", mcp_input, creds)

            else:
                return ToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")

            return ToolResult(tool_name=tool_name, success=True, data=result)

        except Exception as exc:
            logger.error("logistics_agent.tool_failed", tool=tool_name, error=str(exc))
            return ToolResult(tool_name=tool_name, success=False, data=None, error=str(exc))
