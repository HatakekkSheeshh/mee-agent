"""create_task — side-effect tool. Builds a structured task list from explicit
args OR the meeting's MoM action_items. `build_task_items` is the shared
normalizer (also used by the chat graph's reconcile-template builder)."""
from __future__ import annotations

import logging
import unicodedata
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.services.tools._registry import tool

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Lowercase + strip Vietnamese diacritics ("Hiếu" → "hieu") for matching."""
    decomposed = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace("đ", "d")


def assignee_matches(query: str, pic: str) -> bool:
    """True when an assignee filter matches an item's PIC across the
    Redmine-login ↔ display-name gap ("hieunq3" ↔ "Hiếu"): case- and
    diacritic-insensitive, substring in EITHER direction. Deliberately loose —
    the match only narrows an editable HITL template, never writes directly.
    """
    q, p = _norm(query).strip(), _norm(pic).strip()
    if not q or not p:
        return False
    return q in p or p in q


def build_task_items(action_items: list[dict], *, description: str = "") -> list[dict]:
    """Normalize MoM action_items ({pic, deadline, item}) → task items
    ({subject, assignee, due_date, description}). Drops items without text.
    `description` is an optional note stamped on every built task — MoM action
    items carry no description of their own, so the agent/user supplies one."""
    return [
        {
            "subject": ai.get("item", ""),
            "assignee": ai.get("pic", ""),
            "due_date": ai.get("deadline", ""),
            "description": description,
        }
        for ai in action_items
        if ai.get("item")
    ]


def build_agenda_task_items(
    agenda_items: list[dict], *, assignee: str = "", due_date: str = "", description: str = ""
) -> list[dict]:
    """Fallback for an agenda-only MoM (agenda_items present, no action_items):
    one candidate task per agenda topic ({subject=agenda, description=detail}).
    `assignee`/`due_date` are editable defaults stamped on every task — an agenda
    topic has no PIC or deadline of its own. An explicit `description` overrides
    the agenda topic's own detail. Drops topics without a title."""
    return [
        {
            "subject": (a.get("agenda") or "").strip(),
            "assignee": assignee,
            "due_date": due_date,
            "description": description or (a.get("description") or "").strip(),
        }
        for a in agenda_items
        if (a.get("agenda") or "").strip()
    ]


@tool(
    name="create_task",
    description=(
        "Prepare a task (or batch of tasks) to track action items. "
        "Provide an explicit `title` (+ optional assignee/deadline) for a single "
        "task, OR pass only `meeting_id` to build the task list automatically from "
        "the meeting's MoM action_items. "
        "REQUIRES user approval before execution (side-effect)."
    ),
    side_effect=True,
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Single-task subject"},
            "assignee": {"type": "string", "description": "Name or email"},
            "deadline": {"type": "string", "description": "Date, e.g. DD/MM/YYYY"},
            "description": {"type": "string"},
            "meeting_id": {
                "type": "string",
                "format": "uuid",
                "description": "Build tasks from this meeting's MoM action_items",
            },
            "recording_id": {
                "type": "string",
                "format": "uuid",
                "description": (
                    "Scope to ONE recording/phiên: build tasks only from this "
                    "recording's MoM action_items (id from list_recordings). "
                    "Use when the user names a session, e.g. 'trong Meeting 1'. "
                    "Leave empty for the whole project."
                ),
            },
        },
    },
)
async def create_task(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Build a structured task from explicit args OR the meeting's MoM action_items.

    Produces a normalized task list ({subject, assignee, due_date, description}).
    No external write yet — persistence / pm-agent reconcile is a later step; this
    is the HITL-approved producer the chat agent surfaces for confirmation.
    """
    explicit_title = args.get("title") or args.get("subject")
    if explicit_title:
        task = {
            "subject": explicit_title,
            "assignee": args.get("assignee", ""),
            "due_date": args.get("deadline") or args.get("due_date", ""),
            "description": args.get("description", ""),
        }
        logger.info(f"[create_task] explicit task subject={explicit_title!r}")
        return {"status": "prepared", "source": "explicit", "tasks": [task], "count": 1}

    recording_id_str = (args.get("recording_id") or "").strip()
    meeting_id_str = args.get("meeting_id", "")
    if not recording_id_str and not meeting_id_str:
        return {"error": "create_task needs a title, or a meeting_id/recording_id with action_items"}

    if recording_id_str:
        try:
            rid = uuid.UUID(recording_id_str)
        except ValueError:
            return {"error": f"invalid recording_id: {recording_id_str}"}
        mom = await repo.get_recording_mom(session, rid) or {}
        action_items = [ai for ai in (mom.get("action_items") or []) if ai]
        source = "recording_mom"
    else:
        try:
            mid = uuid.UUID(meeting_id_str)
        except ValueError:
            return {"error": f"invalid meeting_id: {meeting_id_str}"}
        action_items = await repo.get_mom_action_items(session, mid)
        source = "mom"

    tasks = build_task_items(action_items, description=(args.get("description") or "").strip())
    assignee = (args.get("assignee") or "").strip()
    if assignee:
        tasks = [t for t in tasks if assignee_matches(assignee, t.get("assignee") or "")]
    if not tasks:
        return {"error": "no action_items found for this scope"}
    logger.info(f"[create_task] built {len(tasks)} task(s) from {source}")
    return {"status": "prepared", "source": source, "tasks": tasks, "count": len(tasks)}
