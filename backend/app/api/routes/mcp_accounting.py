"""
Accounting MCP proxy — self-hosted stand-in, same pattern as mcp_email.py.

No real accounting-software partner account behind this (QuickBooks/Xero/
Zoho require a registered developer app and OAuth approval this project
doesn't have). Real, working implementation instead: a fixed chart of
accounts and vendor list stand in for what would normally be pulled live
from the connected accounting system, and every write (expense, invoice,
journal entry) actually persists (AccountingEntry, models/action_models.py)
— swapping in a genuine partner API later means replacing these handler
bodies, not the MCP contract or the calling agent code.

Contract (same as every self-hosted MCP proxy in this codebase):
    POST /call
    body:     {"tool": "<name>", "arguments": {...}}
    response: {"result": <any>, "error": <str | null>}

Deployment: mounted on this same backend at /mcp/accounting — set
ACCOUNTING_MCP_URL to this app's own base URL.

Auth: no per-user credential (registry.py: credential_key=None,
auth_strategy="none") — protected by X-Internal-MCP-Secret instead.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes.mcp_common import verify_internal_mcp_secret
from app.db.database import AsyncSessionLocal
from app.models.action_models import AccountingEntry

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp/accounting", tags=["MCP: Accounting"])


class MCPCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


def _error_response(message: str) -> dict:
    return {"result": None, "error": message}


def _ok_response(result: Any) -> dict:
    return {"result": result, "error": None}


# Fixed chart of accounts — a real accounting system's chart lives on
# their side, configured per-company.
_ACCOUNTS: list[dict] = [
    {"id": "acct_expense_office", "name": "Office Supplies", "type": "expense"},
    {"id": "acct_expense_travel", "name": "Travel & Entertainment", "type": "expense"},
    {"id": "acct_expense_software", "name": "Software & Subscriptions", "type": "expense"},
    {"id": "acct_expense_professional", "name": "Professional Services", "type": "expense"},
    {"id": "acct_expense_utilities", "name": "Utilities", "type": "expense"},
    {"id": "acct_liability_ap", "name": "Accounts Payable", "type": "liability"},
    {"id": "acct_liability_tax", "name": "Sales Tax Payable", "type": "liability"},
    {"id": "acct_asset_cash", "name": "Cash & Bank", "type": "asset"},
    {"id": "acct_asset_ar", "name": "Accounts Receivable", "type": "asset"},
]

# Curated vendor directory
_VENDORS: list[dict] = [
    {"id": "vendor_001", "name": "CloudHost Inc.", "category": "software"},
    {"id": "vendor_002", "name": "OfficeMart Supplies", "category": "office"},
    {"id": "vendor_003", "name": "Skyline Airlines", "category": "travel"},
    {"id": "vendor_004", "name": "Meridian Legal Partners", "category": "professional_services"},
    {"id": "vendor_005", "name": "CityPower Utilities", "category": "utilities"},
]


@router.post("/call", dependencies=[Depends(verify_internal_mcp_secret)])
async def call_tool(payload: MCPCallRequest):
    handlers = {
        "create_expense": _create_expense,
        "create_invoice": _create_invoice,
        "list_vendors": _list_vendors,
        "get_account_list": _get_account_list,
        "create_journal_entry": _create_journal_entry,
        "export_report": _export_report,
        "get_vendor_history": _get_vendor_history,
    }
    handler = handlers.get(payload.tool)
    if handler is None:
        return _error_response(f"Unknown tool '{payload.tool}'. Supported: {sorted(handlers)}")

    try:
        return await handler(payload.arguments)
    except Exception as exc:
        logger.error("mcp.accounting.unexpected_error", tool=payload.tool, error=str(exc))
        return _error_response(f"Unexpected error handling '{payload.tool}': {exc}")


def _account_exists(account_id: str | None) -> bool:
    return account_id is None or any(a["id"] == account_id for a in _ACCOUNTS)


async def _persist_entry(args: dict, entry_type: str, party_key: str) -> AccountingEntry:
    """Shared write path for create_expense/create_invoice/create_journal_entry."""
    async with AsyncSessionLocal() as db:
        entry = AccountingEntry(
            user_id=args["user_id"],
            entry_type=entry_type,
            party_name=args[party_key],
            amount=args["amount"],
            tax_amount=args.get("tax_amount") or 0,
            currency=args.get("currency", "USD"),
            entry_date=args["invoice_date"] if "invoice_date" in args else args.get("entry_date", ""),
            reference_number=args.get("invoice_number") or args.get("reference_number"),
            account_id=args.get("account_id"),
            description=args.get("description"),
            line_items=args.get("line_items"),
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry


def _entry_out(entry: AccountingEntry) -> dict:
    return {
        "entry_id": entry.id, "entry_type": entry.entry_type, "party_name": entry.party_name,
        "amount": entry.amount, "tax_amount": entry.tax_amount, "currency": entry.currency,
        "entry_date": entry.entry_date, "reference_number": entry.reference_number,
        "status": entry.status,
    }


async def _create_expense(args: dict) -> dict:
    """
    Args: vendor_name, amount, currency, invoice_date (required),
    tax_amount, invoice_number, account_id, description, line_items
    (optional), user_id (injected by finance_agent.py's accounting dispatch).
    """
    required = ["vendor_name", "amount", "currency", "invoice_date"]
    missing = [f for f in required if args.get(f) is None]
    if missing:
        return _error_response(f"create_expense missing required fields: {missing}")
    if not args.get("user_id"):
        return _error_response("create_expense requires 'user_id' (internal — not an agent-supplied field).")
    if not _account_exists(args.get("account_id")):
        return _error_response(f"Unknown account_id '{args.get('account_id')}'. Call list_accounting_accounts first.")

    entry = await _persist_entry(args, "expense", "vendor_name")
    logger.info("mcp.accounting.expense_created", entry_id=entry.id, amount=entry.amount)
    return _ok_response(_entry_out(entry))


async def _create_invoice(args: dict) -> dict:
    """
    Args: customer_name, amount, currency, invoice_date (required),
    tax_amount, invoice_number, description, line_items (optional),
    user_id (injected).
    """
    required = ["customer_name", "amount", "currency", "invoice_date"]
    missing = [f for f in required if args.get(f) is None]
    if missing:
        return _error_response(f"create_invoice missing required fields: {missing}")
    if not args.get("user_id"):
        return _error_response("create_invoice requires 'user_id' (internal — not an agent-supplied field).")

    entry = await _persist_entry(args, "invoice", "customer_name")
    logger.info("mcp.accounting.invoice_created", entry_id=entry.id, amount=entry.amount)
    return _ok_response(_entry_out(entry))


async def _create_journal_entry(args: dict) -> dict:
    """
    Args: description, amount, currency, entry_date (required),
    account_id, reference_number (optional), user_id (injected).
    """
    required = ["description", "amount", "currency", "entry_date"]
    missing = [f for f in required if args.get(f) is None]
    if missing:
        return _error_response(f"create_journal_entry missing required fields: {missing}")
    if not args.get("user_id"):
        return _error_response("create_journal_entry requires 'user_id' (internal — not an agent-supplied field).")
    if not _account_exists(args.get("account_id")):
        return _error_response(f"Unknown account_id '{args.get('account_id')}'. Call get_account_list first.")

    entry = await _persist_entry({**args, "vendor_name": args["description"]}, "journal", "vendor_name")
    logger.info("mcp.accounting.journal_entry_created", entry_id=entry.id, amount=entry.amount)
    return _ok_response(_entry_out(entry))


async def _list_vendors(args: dict) -> dict:
    """Args: none required. Optional 'category' filter."""
    category = args.get("category")
    vendors = _VENDORS if not category else [v for v in _VENDORS if v["category"] == category]
    return _ok_response({"vendors": vendors})


async def _get_account_list(args: dict) -> dict:
    """Args: optional 'account_type' filter (expense|liability|asset)."""
    account_type = args.get("account_type")
    accounts = _ACCOUNTS if not account_type else [a for a in _ACCOUNTS if a["type"] == account_type]
    return _ok_response({"accounts": accounts})


def _vendor_matches(entry_party: str, vendor_name: str) -> bool:
    """
    Invoice vendor strings are rarely byte-identical across documents
    ("CloudHost", "CloudHost Inc.", "CLOUDHOST INC"), so exact equality
    would report "no history" for a vendor the user has clearly paid
    before — the worst possible failure for an anomaly check, since it
    silently turns every invoice into a first-time one. Case-insensitive
    containment either way is the pragmatic middle ground.
    """
    a, b = entry_party.strip().lower(), vendor_name.strip().lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a


async def _get_vendor_history(args: dict) -> dict:
    """
    Args: user_id (injected), vendor_name (optional), limit (optional,
    default 100 entries scanned).

    Read-only spend history for the anomaly check in finance's
    flag_expense_anomalies. Returns the vendor's own prior expenses AND
    the user's overall expense baseline, so the agent can say "3x your
    usual spend with this vendor" from real posted numbers rather than
    inventing a benchmark. A vendor with no history is a legitimate,
    reportable finding in its own right — that's returned as
    matched_entry_count: 0, not an error.
    """
    user_id = args.get("user_id")
    if not user_id:
        return _error_response("get_vendor_history requires 'user_id' (internal — not an agent-supplied field).")
    vendor_name = args.get("vendor_name")
    limit = args.get("limit") or 100

    async with AsyncSessionLocal() as db:
        entries = (await db.execute(
            select(AccountingEntry)
            .where(
                AccountingEntry.user_id == user_id,
                AccountingEntry.entry_type == "expense",
                AccountingEntry.status == "posted",
            )
            .order_by(AccountingEntry.created_at.desc())
            .limit(limit)
        )).scalars().all()

    matched = [
        e for e in entries
        if vendor_name and _vendor_matches(e.party_name or "", vendor_name)
    ]

    def _stats(rows: list[AccountingEntry]) -> dict | None:
        amounts = [e.amount for e in rows if e.amount is not None]
        if not amounts:
            return None
        return {
            "count": len(amounts),
            "average_amount": round(sum(amounts) / len(amounts), 2),
            "min_amount": round(min(amounts), 2),
            "max_amount": round(max(amounts), 2),
        }

    return _ok_response({
        "vendor_name": vendor_name,
        "matched_entry_count": len(matched),
        "vendor_stats": _stats(matched),
        "overall_expense_stats": _stats(entries),
        # Recent matched entries so the agent can cite specific comparisons
        # rather than only quoting an aggregate.
        "recent_vendor_entries": [
            {
                "amount": e.amount, "currency": e.currency,
                "entry_date": e.entry_date, "reference_number": e.reference_number,
                "party_name": e.party_name,
            }
            for e in matched[:10]
        ],
    })


async def _export_report(args: dict) -> dict:
    """
    Args: user_id (injected), optional entry_type filter
    (expense|invoice|journal). Returns a totals summary — a real
    accounting system would generate a formatted PDF/CSV here; this
    returns the underlying numbers for the agent to present.
    """
    user_id = args.get("user_id")
    if not user_id:
        return _error_response("export_report requires 'user_id' (internal — not an agent-supplied field).")
    entry_type = args.get("entry_type")

    async with AsyncSessionLocal() as db:
        q = select(AccountingEntry).where(AccountingEntry.user_id == user_id, AccountingEntry.status == "posted")
        if entry_type:
            q = q.where(AccountingEntry.entry_type == entry_type)
        entries = (await db.execute(q)).scalars().all()

    by_type: dict[str, float] = {}
    for e in entries:
        by_type[e.entry_type] = by_type.get(e.entry_type, 0) + e.amount

    return _ok_response({
        "entry_count": len(entries),
        "totals_by_type": {k: round(v, 2) for k, v in by_type.items()},
        "grand_total": round(sum(by_type.values()), 2),
    })
