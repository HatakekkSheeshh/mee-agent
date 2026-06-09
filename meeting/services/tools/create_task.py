"""create_task — side-effect tool. Builds a structured task list from explicit
args OR the meeting's MoM action_items. `build_task_items` is the shared
normalizer (also used by the chat graph's reconcile-template builder)."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services.tools._registry import tool

logger = logging.getLogger(__name__)


def build_task_items(action_items: list[dict]) -> list[dict]:
    """Normalize MoM action_items ({pic, deadline, item}) → task items
    ({subject, assignee, due_date, description}). Drops items without text."""
    return [
        {
            "subject": ai.get("item", ""),
            "assignee": ai.get("pic", ""),
            "due_date": ai.get("deadline", ""),
            "description": "",
        }
        for ai in action_items
        if ai.get("item")
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

    meeting_id_str = args.get("meeting_id", "")
    if not meeting_id_str:
        return {"error": "create_task needs a title, or a meeting_id with action_items"}
    try:
        mid = uuid.UUID(meeting_id_str)
    except ValueError:
        return {"error": f"invalid meeting_id: {meeting_id_str}"}

    action_items = await repo.get_mom_action_items(session, mid)
    tasks = build_task_items(action_items)
    if not tasks:
        return {"error": "no action_items found in this meeting's MoM"}
    logger.info(f"[create_task] built {len(tasks)} task(s) from MoM action_items")
    return {"status": "prepared", "source": "mom", "tasks": tasks, "count": len(tasks)}
