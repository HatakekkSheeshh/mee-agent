"""recording_mom — safe read tool. Return ONE recording's structured MoM."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services.tools._registry import tool

logger = logging.getLogger(__name__)


@tool(
    name="recording_mom",
    description=(
        "Read ONE recording's minutes (MoM): summary, decisions, and "
        "action_items (each with `pic`/người phụ trách, deadline, item). "
        "Use after `list_recordings` to answer questions scoped to a "
        "specific recording — e.g. 'việc của Hiếu trong phiên X' — by reading "
        "that recording's action_items and filtering by `pic`. Only attribute "
        "a fact to the recording you actually read. "
        "Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["recording_id"],
        "properties": {
            "recording_id": {
                "type": "string",
                "format": "uuid",
                "description": "From list_recordings",
            },
        },
    },
)
async def recording_mom(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Safe read tool — return ONE recording's structured MoM (summary,
    decisions, action_items with `pic`). Use after list_recordings to answer
    questions scoped to a specific recording (e.g. "X's tasks in recording Y")
    without mixing in other recordings' data."""
    rid_str = args.get("recording_id", "")
    if not rid_str:
        return {"error": "recording_id required"}
    try:
        rid = uuid.UUID(rid_str)
    except ValueError:
        return {"error": f"invalid recording_id: {rid_str}"}

    mom = await repo.get_recording_mom(session, rid)
    if not mom:
        return {"status": "not_found", "recording_id": rid_str}
    return {"status": "ok", "recording_id": rid_str, "mom": mom}
