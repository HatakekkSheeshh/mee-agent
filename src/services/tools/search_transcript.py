"""search_transcript — safe read tool. Keyword match over a meeting's transcript."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="search_transcript",
    description=(
        "Search within a meeting's transcript for keyword matches. "
        "Use when user asks 'when did X say Y' or 'did anyone mention Z'. "
        "Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["meeting_id", "query"],
        "properties": {
            "meeting_id": {"type": "string", "format": "uuid"},
            "query": {"type": "string"},
        },
    },
)
async def search_transcript(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Safe tool — search through segments of a specific meeting."""
    meeting_id_str = args.get("meeting_id", "")
    query = args.get("query", "").lower()
    if not meeting_id_str or not query:
        return {"error": "meeting_id and query required"}

    transcript = await repo.join_meeting_transcript(session, uuid.UUID(meeting_id_str))
    # Simple keyword match — Phase F sẽ dùng semantic search Memory Service
    lines = [ln.strip() for ln in transcript.split("\n") if query in ln.lower()]
    return {
        "status": "ok",
        "query": query,
        "matches": lines[:10],
        "match_count": len(lines),
    }
