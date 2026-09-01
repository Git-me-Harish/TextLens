"""
Pharmacy MCP proxy — self-hosted stand-in, same pattern as mcp_email.py.

There's no real pharmacy partner account behind this (unlike Google
Calendar, which is a genuine per-user OAuth connection to Google). Rather
than leave pharmacy_api pointing at an unreachable placeholder URL, this
is a real, working implementation backed by this app's own database
(PharmacyOrder, models/action_models.py) — orders actually persist,
statuses actually track, searches run against a real (if curated) medicine
catalog. Swapping in a genuine partner API later means replacing the
bodies of these handlers; the MCP contract and the calling agent code
don't change.

Contract (same as every self-hosted MCP proxy in this codebase):
    POST /call
    body:     {"tool": "<name>", "arguments": {...}}
    response: {"result": <any>, "error": <str | null>}

Deployment: mounted on this same backend at /mcp/pharmacy — set
PHARMACY_MCP_URL to this app's own base URL, same pattern as
GOOGLE_CALENDAR_MCP_URL / EMAIL_MCP_URL.

Auth: no per-user credential (registry.py: credential_key=None,
auth_strategy="none") — protected by the X-Internal-MCP-Secret check
instead, same reasoning as mcp_email.py.
"""

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes.mcp_common import verify_internal_mcp_secret
from app.db.database import AsyncSessionLocal
from app.models.action_models import PharmacyOrder

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp/pharmacy", tags=["MCP: Pharmacy"])


class MCPCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


def _error_response(message: str) -> dict:
    return {"result": None, "error": message}


def _ok_response(result: Any) -> dict:
    return {"result": result, "error": None}


# Curated medicine catalog — a real pharmacy's inventory lives on their
# side, not ours. Prices in USD cents-free floats.
_MEDICINE_CATALOG: list[dict] = [
    {"id": "med_001", "name": "Amoxicillin", "dosages": ["250mg", "500mg"], "price": 12.50, "in_stock": True, "requires_prescription": True},
    {"id": "med_002", "name": "Ibuprofen", "dosages": ["200mg", "400mg", "600mg"], "price": 6.20, "in_stock": True, "requires_prescription": False},
    {"id": "med_003", "name": "Metformin", "dosages": ["500mg", "850mg", "1000mg"], "price": 9.80, "in_stock": True, "requires_prescription": True},
    {"id": "med_004", "name": "Atorvastatin", "dosages": ["10mg", "20mg", "40mg"], "price": 15.30, "in_stock": True, "requires_prescription": True},
    {"id": "med_005", "name": "Omeprazole", "dosages": ["20mg", "40mg"], "price": 11.00, "in_stock": True, "requires_prescription": False},
    {"id": "med_006", "name": "Amlodipine", "dosages": ["2.5mg", "5mg", "10mg"], "price": 8.40, "in_stock": True, "requires_prescription": True},
    {"id": "med_007", "name": "Azithromycin", "dosages": ["250mg", "500mg"], "price": 18.90, "in_stock": False, "requires_prescription": True},
    {"id": "med_008", "name": "Cetirizine", "dosages": ["5mg", "10mg"], "price": 5.50, "in_stock": True, "requires_prescription": False},
    {"id": "med_009", "name": "Paracetamol", "dosages": ["500mg", "650mg"], "price": 4.00, "in_stock": True, "requires_prescription": False},
    {"id": "med_010", "name": "Losartan", "dosages": ["25mg", "50mg", "100mg"], "price": 10.70, "in_stock": True, "requires_prescription": True},
]


def _find_medicine(name: str) -> dict | None:
    needle = name.strip().lower()
    for med in _MEDICINE_CATALOG:
        if med["name"].lower() == needle or needle in med["name"].lower():
            return med
    return None


@router.post("/call", dependencies=[Depends(verify_internal_mcp_secret)])
async def call_tool(payload: MCPCallRequest):
    handlers = {
        "search_medicines": _search_medicines,
        "check_medicine_availability": _check_medicine_availability,
        "get_medicine_details": _get_medicine_details,
        "create_order": _create_order,
        "get_order_status": _get_order_status,
        "get_order_history": _get_order_history,
        "cancel_order": _cancel_order,
    }
    handler = handlers.get(payload.tool)
    if handler is None:
        return _error_response(f"Unknown tool '{payload.tool}'. Supported: {sorted(handlers)}")

    try:
        return await handler(payload.arguments)
    except Exception as exc:
        logger.error("mcp.pharmacy.unexpected_error", tool=payload.tool, error=str(exc))
        return _error_response(f"Unexpected error handling '{payload.tool}': {exc}")


async def _search_medicines(args: dict) -> dict:
    """Args: medicine_name (required), dosage (optional filter)."""
    name = args.get("medicine_name")
    if not name:
        return _error_response("search_medicines requires 'medicine_name'.")
    needle = name.strip().lower()
    matches = [m for m in _MEDICINE_CATALOG if needle in m["name"].lower()]
    dosage = args.get("dosage")
    if dosage:
        matches = [m for m in matches if dosage in m["dosages"]]
    return _ok_response({
        "query": name,
        "results": [
            {"medicine_id": m["id"], "name": m["name"], "available_dosages": m["dosages"],
             "price": m["price"], "in_stock": m["in_stock"],
             "requires_prescription": m["requires_prescription"]}
            for m in matches
        ],
    })


async def _check_medicine_availability(args: dict) -> dict:
    """Args: medicine_name (required)."""
    name = args.get("medicine_name")
    if not name:
        return _error_response("check_medicine_availability requires 'medicine_name'.")
    med = _find_medicine(name)
    if not med:
        return _ok_response({"medicine_name": name, "found": False, "in_stock": False})
    return _ok_response({
        "medicine_name": med["name"], "found": True,
        "in_stock": med["in_stock"], "price": med["price"],
    })


async def _get_medicine_details(args: dict) -> dict:
    """Args: medicine_name (required)."""
    name = args.get("medicine_name")
    if not name:
        return _error_response("get_medicine_details requires 'medicine_name'.")
    med = _find_medicine(name)
    if not med:
        return _error_response(f"No medicine found matching '{name}'.")
    return _ok_response(med)


async def _create_order(args: dict) -> dict:
    """
    Args: items (required, [{medicine_name, dosage, quantity}]),
    delivery_address_id (optional), user_id (injected by the calling
    agent — see healthcare_agent.py's pharmacy dispatch).
    """
    items = args.get("items")
    if not items or not isinstance(items, list):
        return _error_response("create_order requires a non-empty 'items' array.")
    user_id = args.get("user_id")
    if not user_id:
        return _error_response("create_order requires 'user_id' (internal — not an agent-supplied field).")

    priced_items, total = [], 0.0
    for item in items:
        name = item.get("medicine_name")
        qty = item.get("quantity", 1)
        if not name or qty < 1:
            return _error_response(f"Invalid order item: {item}")
        med = _find_medicine(name)
        if not med:
            return _error_response(f"Medicine '{name}' not found in catalog.")
        if not med["in_stock"]:
            return _error_response(f"'{med['name']}' is currently out of stock.")
        line_total = round(med["price"] * qty, 2)
        total += line_total
        priced_items.append({
            "medicine_name": med["name"], "dosage": item.get("dosage") or (med["dosages"][0] if med["dosages"] else None),
            "quantity": qty, "unit_price": med["price"], "line_total": line_total,
        })

    async with AsyncSessionLocal() as db:
        order = PharmacyOrder(
            user_id=user_id, items=priced_items, total_amount=round(total, 2),
            delivery_address_id=args.get("delivery_address_id"), status="confirmed",
        )
        db.add(order)
        await db.commit()
        await db.refresh(order)

    logger.info("mcp.pharmacy.order_created", order_id=order.id, items=len(priced_items), total=order.total_amount)
    return _ok_response({
        "order_id": order.id, "status": order.status, "items": priced_items,
        "total_amount": order.total_amount, "currency": order.currency,
    })


async def _get_order_status(args: dict) -> dict:
    """Args: order_id (required), user_id (injected)."""
    order_id, user_id = args.get("order_id"), args.get("user_id")
    if not order_id:
        return _error_response("get_order_status requires 'order_id'.")
    async with AsyncSessionLocal() as db:
        q = select(PharmacyOrder).where(PharmacyOrder.id == order_id)
        if user_id:
            q = q.where(PharmacyOrder.user_id == user_id)
        order = (await db.execute(q)).scalar_one_or_none()
    if not order:
        return _error_response(f"Order '{order_id}' not found.")
    return _ok_response({
        "order_id": order.id, "status": order.status, "total_amount": order.total_amount,
        "items": order.items, "created_at": order.created_at.isoformat(),
    })


async def _get_order_history(args: dict) -> dict:
    """
    Args: user_id (injected), optional limit (default 50).

    Returns the distinct medicines this user has actually ordered through
    the platform, most recently ordered first. This is what grounds
    healthcare's check_medication_interactions in a real medication list —
    without it the agent would be reasoning about drugs the user merely
    might be taking. Cancelled orders are excluded: a cancelled order is
    not something the patient is on.
    """
    user_id = args.get("user_id")
    if not user_id:
        return _error_response("get_order_history requires 'user_id' (internal — not an agent-supplied field).")
    limit = args.get("limit") or 50

    async with AsyncSessionLocal() as db:
        orders = (await db.execute(
            select(PharmacyOrder)
            .where(PharmacyOrder.user_id == user_id, PharmacyOrder.status != "cancelled")
            .order_by(PharmacyOrder.created_at.desc())
            .limit(limit)
        )).scalars().all()

    # Collapse to one row per medicine — a repeat order of the same drug is
    # the same medication, not a second one. dict preserves insertion order,
    # so first-seen (= most recent, given the ordering above) wins.
    medications: dict[str, dict] = {}
    for order in orders:
        for item in (order.items or []):
            name = (item.get("medicine_name") or "").strip()
            if not name or name.lower() in medications:
                continue
            medications[name.lower()] = {
                "medicine_name": name,
                "dosage": item.get("dosage"),
                "last_ordered": order.created_at.isoformat(),
            }

    return _ok_response({
        "order_count": len(orders),
        "medications": list(medications.values()),
    })


async def _cancel_order(args: dict) -> dict:
    """Args: order_id (required), user_id (injected)."""
    order_id, user_id = args.get("order_id"), args.get("user_id")
    if not order_id:
        return _error_response("cancel_order requires 'order_id'.")
    async with AsyncSessionLocal() as db:
        q = select(PharmacyOrder).where(PharmacyOrder.id == order_id)
        if user_id:
            q = q.where(PharmacyOrder.user_id == user_id)
        order = (await db.execute(q)).scalar_one_or_none()
        if not order:
            return _error_response(f"Order '{order_id}' not found.")
        if order.status in ("shipped", "delivered"):
            return _error_response(f"Order '{order_id}' has already been {order.status} and can no longer be cancelled.")
        order.status = "cancelled"
        order.updated_at = datetime.utcnow()
        await db.commit()
    logger.info("mcp.pharmacy.order_cancelled", order_id=order_id)
    return _ok_response({"order_id": order_id, "status": "cancelled"})
