"""switch_meeting — safe read tool. Resolve a project by title and return its id."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="switch_meeting",
    description=(
        "Switch the active project/meeting by title when the user asks about a "
        "DIFFERENT project than the current one. Returns the matched meeting(s). "
        "After switching, use `retrieve` to read that project's content. "
        "Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "description": "Project/meeting title fragment"},
        },
    },
)
async def switch_meeting(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Safe read tool — resolve a project the user names by title and return its
    id (most-recent match) so the agent can re-scope subsequent retrieval."""
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    matches = await repo.find_meetings_by_title(session, user_id, title)
    if not matches:
        return {"status": "not_found", "title": title, "candidates": []}
    return {
        "status": "ok",
        "meeting_id": str(matches[0].id),
        "candidates": [{"id": str(m.id), "title": m.title} for m in matches],
    }
