"""list_recordings — safe read tool. Enumerate a meeting/project's recordings."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="list_recordings",
    description=(
        "List the recordings (phiên/buổi họp) of the current meeting (cuộc họp), with "
        "each recording's label, date, and whether it has minutes (MoM). "
        "Call this FIRST when the user asks about a SPECIFIC recording / "
        "phiên / 'Meeting N' / a date, to resolve which recording they mean "
        "before reading it with `recording_mom`. "
        "Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "properties": {
            "meeting_id": {"type": "string", "format": "uuid"},
        },
    },
)
async def list_recordings(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Safe read tool — enumerate a meeting/project's recordings (phiên) so the
    agent can map "Meeting 1"/ordinal/date → recording_id before reading one
    recording's MoM. meeting_id is auto-injected server-side."""
    meeting_id_str = args.get("meeting_id", "")
    if not meeting_id_str:
        return {"error": "meeting_id required"}
    try:
        mid = uuid.UUID(meeting_id_str)
    except ValueError:
        return {"error": f"invalid meeting_id: {meeting_id_str}"}

    recordings = await repo.list_recordings(session, mid)
    return {"status": "ok", "recordings": recordings, "count": len(recordings)}
