"""
Email MCP proxy — sends through the platform's own Resend account.

Unlike Google Calendar (per-user OAuth), email_api has no per-user credential
at all — see registry.py's MCP_REGISTRY["email_api"] (auth_strategy="none",
credential_key=None). Every user's "send a payment reminder" / "email the
applicant" action goes through this app's own settings.RESEND_API_KEY, the
same account app/services/email_service.py already uses for job-completion
notifications. Reuses that module's resend SDK initialization.

Because there's no per-user token gating access (unlike Calendar, where a
caller needs a real Google bearer token to do anything), this route is
protected by the X-Internal-MCP-Secret check instead — see mcp_common.py and
INTERNAL_MCP_SHARED_SECRET. Without that, this would be an open relay for
this app's email quota/reputation.

Contract (same as every self-hosted MCP proxy in this codebase):
    POST /call
    body:     {"tool": "<name>", "arguments": {...}}
    response: {"result": <any>, "error": <str | null>}

Deployment: mounted on this same backend at /mcp/email — set EMAIL_MCP_URL
to this app's own base URL, same pattern as GOOGLE_CALENDAR_MCP_URL.
"""

import asyncio
from typing import Any

import resend
import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.routes.mcp_common import verify_internal_mcp_secret
from app.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp/email", tags=["MCP: Email"])


class MCPCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


def _error_response(message: str) -> dict:
    return {"result": None, "error": message}


def _ok_response(result: Any) -> dict:
    return {"result": result, "error": None}


@router.post("/call", dependencies=[Depends(verify_internal_mcp_secret)])
async def call_tool(payload: MCPCallRequest):
    if not settings.RESEND_API_KEY:
        return _error_response(
            "Email sending is not configured on this server (RESEND_API_KEY is empty)."
        )
    resend.api_key = settings.RESEND_API_KEY

    handlers = {
        "send_email": _send_email,
        "send_email_with_attachment": _send_email_with_attachment,
        "draft_email": _draft_email,
    }
    handler = handlers.get(payload.tool)
    if handler is None:
        return _error_response(
            f"Unknown tool '{payload.tool}'. Supported: {sorted(handlers)}"
        )

    try:
        return await handler(payload.arguments)
    except Exception as exc:
        logger.error("mcp.email.unexpected_error", tool=payload.tool, error=str(exc))
        return _error_response(f"Unexpected error handling '{payload.tool}': {exc}")


def _validate_send_args(args: dict) -> str | None:
    """Returns an error message, or None if args are valid."""
    if not args.get("to"):
        return "requires 'to' (recipient email address)."
    if not args.get("subject"):
        return "requires 'subject'."
    if not args.get("body"):
        return "requires 'body'."
    return None


async def _send_email(args: dict) -> dict:
    """Args: to (required), subject (required), body (required, plain text)."""
    err = _validate_send_args(args)
    if err:
        return _error_response(f"send_email {err}")

    try:
        # resend.Emails.send is a blocking HTTP call — run off the event loop
        result = await asyncio.to_thread(
            resend.Emails.send,
            {
                "from": settings.FROM_EMAIL,
                "to": [args["to"]],
                "subject": args["subject"],
                "text": args["body"],
            },
        )
    except Exception as exc:
        logger.error("mcp.email.send_failed", to=args["to"], error=str(exc))
        return _error_response(f"Resend API error: {exc}")

    email_id = result.get("id") if isinstance(result, dict) else None
    logger.info("mcp.email.sent", to=args["to"], subject=args["subject"][:60], email_id=email_id)
    return _ok_response({"email_id": email_id, "to": args["to"], "subject": args["subject"]})


async def _send_email_with_attachment(args: dict) -> dict:
    """
    Args: to, subject, body (all required, as send_email), plus
    attachment_filename (required) and attachment_content_base64 (required,
    base64-encoded file content — Resend expects raw bytes, decoded below).
    """
    err = _validate_send_args(args)
    if err:
        return _error_response(f"send_email_with_attachment {err}")
    filename = args.get("attachment_filename")
    content_b64 = args.get("attachment_content_base64")
    if not filename or not content_b64:
        return _error_response(
            "send_email_with_attachment requires 'attachment_filename' and "
            "'attachment_content_base64'."
        )

    import base64
    try:
        content_bytes = base64.b64decode(content_b64)
    except Exception:
        return _error_response("attachment_content_base64 is not valid base64.")

    try:
        result = await asyncio.to_thread(
            resend.Emails.send,
            {
                "from": settings.FROM_EMAIL,
                "to": [args["to"]],
                "subject": args["subject"],
                "text": args["body"],
                "attachments": [{"filename": filename, "content": list(content_bytes)}],
            },
        )
    except Exception as exc:
        logger.error("mcp.email.send_with_attachment_failed", to=args["to"], error=str(exc))
        return _error_response(f"Resend API error: {exc}")

    email_id = result.get("id") if isinstance(result, dict) else None
    logger.info("mcp.email.sent_with_attachment", to=args["to"], email_id=email_id)
    return _ok_response({"email_id": email_id, "to": args["to"], "subject": args["subject"]})


async def _draft_email(args: dict) -> dict:
    """
    Args: to, subject, body (all required). Does NOT send — returns the
    composed draft for the agent to present to the user before a separate
    send_email call. No external call is made.
    """
    err = _validate_send_args(args)
    if err:
        return _error_response(f"draft_email {err}")
    return _ok_response({
        "to": args["to"],
        "subject": args["subject"],
        "body": args["body"],
        "sent": False,
    })
