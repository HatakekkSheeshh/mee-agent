"""switch_meeting — safe read tool. Resolve a project by title and return its id."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.services.meeting_resolver import default_generate, llm_resolve_meeting
from src.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="switch_meeting",
    description=(
        "Switch the active meeting (cuộc họp) by title when the user asks about a "
        "DIFFERENT meeting than the current one. Returns the matched meeting(s). "
        "After switching, read its content with `list_recordings` / `recording_mom`. "
        "A meeting is a Mee container — NOT a Redmine project. "
        "Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "description": "Meeting (cuộc họp) title fragment"},
        },
    },
)
async def switch_meeting(
    args: dict, *, session: AsyncSession, user_id: uuid.UUID, generate=None
) -> dict:
    """Safe read tool — resolve a project the user names by title and return its
    id so the agent can re-scope subsequent retrieval.

    Exactly 1 ILIKE match → use it (no LLM). On 0 or >1 matches, LLM-resolve over
    the user's meetings by title (handles acronyms/abbreviations ILIKE can't, e.g.
    "GIP"). No confident match → not_found WITH candidates so the agent can offer
    near-matches instead of creating a new project."""
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    matches = await repo.find_meetings_by_title(session, user_id, title)
    if len(matches) == 1:
        m = matches[0]
        return {
            "status": "ok",
            "meeting_id": str(m.id),
            "candidates": [{"id": str(m.id), "title": m.title}],
        }

    # Ambiguous (0 or >1): LLM-resolve over ALL the user's meetings.
    candidates = await repo.list_meetings_for_user(session, user_id)
    candidate_dicts = [{"id": str(m.id), "title": m.title} for m in candidates]
    chosen_id = llm_resolve_meeting(
        title, candidates, generate=generate or default_generate
    ) if candidates else None
    if chosen_id:
        return {"status": "ok", "meeting_id": chosen_id, "candidates": candidate_dicts}
    return {"status": "not_found", "title": title, "candidates": candidate_dicts}
