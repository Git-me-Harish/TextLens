"""
Google Calendar MCP proxy.

This is the first real implementation of the simplified "MCP" contract that
app/services/mcp/registry.py's call_mcp_tool() expects — NOT the official
Model Context Protocol (JSON-RPC over stdio/SSE). The contract here is the
one this codebase actually defined:

    POST {base_url}/call
    body:     {"tool": "<name>", "arguments": {...}}
    response: {"result": <any>, "error": <str | null>}

Auth: the caller (registry.py) forwards the *user's* Google OAuth access
token as `Authorization: Bearer <token>` — not our own app's JWT. This route
is intentionally NOT behind get_current_user: it's an internal service
boundary, authenticated entirely by whatever Google Calendar itself accepts
on the forwarded token. A request with an invalid/expired token simply fails
against Google's API and comes back as a normal tool error.

Tool → argument shapes below match exactly what healthcare_agent.py and
legal_agent.py actually send (see their _execute_tool methods) — this isn't
a speculative general-purpose calendar API wrapper, it implements what the
agents call today plus the remaining registry.py allowlist entries
(update_event, delete_event, check_availability) for completeness.

Known simplification: no token-refresh handling. If the user's stored
access_token has expired, Google returns 401, which this proxy passes
through as an auth error — registry.py already surfaces that as "check your
connected credentials," prompting the user to reconnect. Refreshing and
persisting a new access_token would require this route to reach back into
the credential store, which breaks the clean boundary (this route never
touches the DB). Revisit if expiring tokens turn out to be a real problem
in practice.

Deployment: mounted on this same FastAPI app at /mcp/google-calendar — set
GOOGLE_CALENDAR_MCP_URL to this app's own base URL (e.g.
http://localhost:8000/mcp/google-calendar, or the docker-compose service
name). No separate service to deploy.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api.routes.mcp_common import verify_internal_mcp_secret

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/mcp/google-calendar", tags=["MCP: Google Calendar"])

_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_DEFAULT_TIMEZONE = "UTC"


class MCPCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


def _error_response(message: str) -> dict:
    return {"result": None, "error": message}


def _ok_response(result: Any) -> dict:
    return {"result": result, "error": None}


@router.post("/call", dependencies=[Depends(verify_internal_mcp_secret)])
async def call_tool(payload: MCPCallRequest, authorization: str | None = Header(default=None)):
    """
    Single entry point — dispatches to the Google Calendar API based on
    `tool`. Mirrors the {result, error} contract registry.py parses.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return _error_response("Missing or malformed Authorization bearer token.")
    access_token = authorization.split(" ", 1)[1].strip()
    if not access_token:
        return _error_response("Empty bearer token.")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    handlers = {
        "create_event": _create_event,
        "list_events": _list_events,
        "update_event": _update_event,
        "delete_event": _delete_event,
        "check_availability": _check_availability,
        "find_free_slots": _find_free_slots,
    }
    handler = handlers.get(payload.tool)
    if handler is None:
        return _error_response(
            f"Unknown tool '{payload.tool}'. Supported: {sorted(handlers)}"
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            return await handler(client, headers, payload.arguments)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = _extract_google_error(exc.response)
        log_fn = logger.warning if status == 401 else logger.error
        log_fn("mcp.google_calendar.api_error", status=status, detail=detail)
        return _error_response(f"Google Calendar API error ({status}): {detail}")
    except httpx.RequestError as exc:
        logger.error("mcp.google_calendar.request_failed", error=str(exc))
        return _error_response(f"Could not reach Google Calendar API: {exc}")
    except Exception as exc:
        logger.error("mcp.google_calendar.unexpected_error", tool=payload.tool, error=str(exc))
        return _error_response(f"Unexpected error handling '{payload.tool}': {exc}")


def _extract_google_error(response: httpx.Response) -> str:
    try:
        data = response.json()
        return data.get("error", {}).get("message", response.text[:300])
    except Exception:
        return response.text[:300]


# ── Tool implementations ────────────────────────────────────────────────────

async def _create_event(client: httpx.AsyncClient, headers: dict, args: dict) -> dict:
    """
    Args (from healthcare_agent.create_calendar_event / legal_agent.create_deadline_event):
      title (required), start_datetime (required, ISO 8601), end_datetime (required, ISO 8601),
      description?, location?, reminders? (list of {"minutes_before": int}), timezone? (default UTC)
    """
    title = args.get("title")
    start_dt = args.get("start_datetime")
    end_dt = args.get("end_datetime")
    if not title or not start_dt or not end_dt:
        return _error_response("create_event requires 'title', 'start_datetime', 'end_datetime'.")

    tz = args.get("timezone", _DEFAULT_TIMEZONE)
    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start_dt, "timeZone": tz},
        "end": {"dateTime": end_dt, "timeZone": tz},
    }
    if args.get("description"):
        body["description"] = args["description"]
    if args.get("location"):
        body["location"] = args["location"]

    reminders = args.get("reminders")
    if reminders:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": r.get("minutes_before", 60)}
                for r in reminders
            ],
        }

    resp = await client.post(f"{_CALENDAR_API}/calendars/primary/events", headers=headers, json=body)
    resp.raise_for_status()
    event = resp.json()
    return _ok_response({
        "event_id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "start": event.get("start"),
        "end": event.get("end"),
        "status": event.get("status"),
    })


async def _list_events(client: httpx.AsyncClient, headers: dict, args: dict) -> dict:
    """
    Args (from legal_agent.list_upcoming_legal_events / general use):
      days_ahead? (default 180), max_results? (default 25)
    """
    days_ahead = int(args.get("days_ahead", 180))
    max_results = int(args.get("max_results", 25))
    now = datetime.now(timezone.utc)
    params = {
        "timeMin": now.isoformat(),
        "timeMax": (now + timedelta(days=days_ahead)).isoformat(),
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    resp = await client.get(f"{_CALENDAR_API}/calendars/primary/events", headers=headers, params=params)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return _ok_response([
        {
            "event_id": e.get("id"),
            "title": e.get("summary"),
            "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
            "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
        }
        for e in items
    ])


async def _update_event(client: httpx.AsyncClient, headers: dict, args: dict) -> dict:
    """Args: event_id (required), plus any of title/start_datetime/end_datetime/description/location."""
    event_id = args.get("event_id")
    if not event_id:
        return _error_response("update_event requires 'event_id'.")

    tz = args.get("timezone", _DEFAULT_TIMEZONE)
    patch: dict[str, Any] = {}
    if args.get("title"):
        patch["summary"] = args["title"]
    if args.get("start_datetime"):
        patch["start"] = {"dateTime": args["start_datetime"], "timeZone": tz}
    if args.get("end_datetime"):
        patch["end"] = {"dateTime": args["end_datetime"], "timeZone": tz}
    if args.get("description") is not None:
        patch["description"] = args["description"]
    if args.get("location") is not None:
        patch["location"] = args["location"]
    if not patch:
        return _error_response("update_event requires at least one field to change.")

    resp = await client.patch(
        f"{_CALENDAR_API}/calendars/primary/events/{event_id}", headers=headers, json=patch
    )
    resp.raise_for_status()
    event = resp.json()
    return _ok_response({"event_id": event.get("id"), "status": event.get("status")})


async def _delete_event(client: httpx.AsyncClient, headers: dict, args: dict) -> dict:
    """Args: event_id (required)."""
    event_id = args.get("event_id")
    if not event_id:
        return _error_response("delete_event requires 'event_id'.")

    resp = await client.delete(f"{_CALENDAR_API}/calendars/primary/events/{event_id}", headers=headers)
    if resp.status_code not in (200, 204):
        resp.raise_for_status()
    return _ok_response({"event_id": event_id, "deleted": True})


async def _check_availability(client: httpx.AsyncClient, headers: dict, args: dict) -> dict:
    """Args: time_min (required, ISO 8601), time_max (required, ISO 8601)."""
    time_min = args.get("time_min")
    time_max = args.get("time_max")
    if not time_min or not time_max:
        return _error_response("check_availability requires 'time_min' and 'time_max'.")

    busy = await _free_busy(client, headers, time_min, time_max)
    return _ok_response({"busy": busy, "is_free": len(busy) == 0})


async def _find_free_slots(client: httpx.AsyncClient, headers: dict, args: dict) -> dict:
    """
    Args (from healthcare_agent.check_calendar_availability):
      preferred_date? (ISO date, default: search the next 14 days),
      duration_minutes? (default 30)
    Returns free windows of at least duration_minutes, within working hours
    (09:00-18:00 in the requested/default timezone) over the search window.
    """
    duration_minutes = int(args.get("duration_minutes", 30))
    tz_name = args.get("timezone", _DEFAULT_TIMEZONE)
    preferred_date = args.get("preferred_date")

    if preferred_date:
        window_start = datetime.fromisoformat(preferred_date).replace(
            hour=9, minute=0, second=0, tzinfo=timezone.utc
        )
        window_end = window_start.replace(hour=18)
        search_windows = [(window_start, window_end)]
    else:
        today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
        search_windows = [
            (today + timedelta(days=d), today + timedelta(days=d, hours=9))
            for d in range(14)
        ]

    overall_min = search_windows[0][0]
    overall_max = search_windows[-1][1]
    busy = await _free_busy(client, headers, overall_min.isoformat(), overall_max.isoformat())
    busy_intervals = sorted(
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in busy
    )

    free_slots = []
    for win_start, win_end in search_windows:
        cursor = win_start
        for b_start, b_end in busy_intervals:
            if b_end <= cursor or b_start >= win_end:
                continue
            if b_start > cursor and (b_start - cursor) >= timedelta(minutes=duration_minutes):
                free_slots.append((cursor, b_start))
            cursor = max(cursor, b_end)
        if win_end > cursor and (win_end - cursor) >= timedelta(minutes=duration_minutes):
            free_slots.append((cursor, win_end))
        if len(free_slots) >= 10:
            break

    return _ok_response({
        "timezone": tz_name,
        "duration_minutes": duration_minutes,
        "free_slots": [
            {"start": s.isoformat(), "end": e.isoformat()} for s, e in free_slots[:10]
        ],
    })


async def _free_busy(
    client: httpx.AsyncClient, headers: dict, time_min: str, time_max: str
) -> list[dict]:
    body = {"timeMin": time_min, "timeMax": time_max, "items": [{"id": "primary"}]}
    resp = await client.post(f"{_CALENDAR_API}/freeBusy", headers=headers, json=body)
    resp.raise_for_status()
    calendars = resp.json().get("calendars", {})
    primary = calendars.get("primary", {})
    return primary.get("busy", [])
