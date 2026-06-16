"""list_meetings — safe read tool. Enumerate the user's meetings (cuộc họp).

A *meeting* (cuộc họp) is a Mee container — it holds recordings (phiên) and their
minutes (MoM). This is NOT a Redmine *project*; do not answer "what meetings do I
have" with `get_redmine_projects`. Soft-deleted meetings are excluded (the repo
filters `deleted_at IS NULL`).
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="list_meetings",
    description=(
        "List the user's meetings (cuộc họp) in Mee — each with its title and id. "
        "Call this when the user asks which meetings/cuộc họp they have, or to pick "
        "which meeting to read. A meeting is a Mee container of recordings (phiên) "
        "and minutes (MoM) — it is NOT a Redmine project, so do NOT use "
        "`get_redmine_projects` for this. Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={"type": "object", "properties": {}},
)
async def list_meetings(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Safe read tool — enumerate the signed-in user's meetings (cuộc họp). The
    repo scopes to the user's active memberships and excludes soft-deleted rows."""
    meetings = await repo.list_meetings_for_user(session, user_id)
    return {
        "status": "ok",
        "meetings": [{"id": str(m.id), "title": m.title} for m in meetings],
        "count": len(meetings),
    }
