"""retrieve — safe read tool. Hybrid retrieval over a meeting's memory_events,
falling back to the meeting's MoM text when no embeddings exist yet."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.services.tools._registry import tool

logger = logging.getLogger(__name__)

# Default number of retrieved chunks for the `retrieve` tool.
DEFAULT_RETRIEVE_K = 5


def _mom_to_chunks(meeting: Any) -> list[dict]:
    """Flatten a meeting's recording MoMs into retrievable text chunks.

    Fallback for `retrieve` when no embeddings exist for the meeting yet.
    Reads the note_generator MoM shape: summary, decisions[], action_items[]
    ({pic, deadline, item}).
    """
    chunks: list[dict] = []
    for rec in (getattr(meeting, "recordings", None) or []):
        mom = getattr(rec, "mom_json", None)
        if not mom:
            continue
        label = (
            getattr(rec, "title", None)
            or getattr(rec, "session_label", None)
            or "phiên"
        )
        summary = mom.get("summary")
        if summary:
            chunks.append({"text": summary, "type": "summary", "source_label": label})
        for d in mom.get("decisions", []) or []:
            txt = d.get("description") or d.get("topic") or ""
            if txt:
                chunks.append({"text": txt, "type": "decision", "source_label": label})
        for a in mom.get("action_items", []) or []:
            item = a.get("item") or ""
            if not item:
                continue
            pic, deadline = a.get("pic", ""), a.get("deadline", "")
            txt = item if not (pic or deadline) else f"{item} (PIC: {pic}, hạn: {deadline})"
            chunks.append({"text": txt, "type": "action_item", "source_label": label})
    return chunks


@tool(
    name="retrieve",
    description=(
        "Search the current meeting/project's minutes (MoM) and transcript "
        "for content relevant to a query. Call this FIRST whenever you need "
        "meeting content to answer a question — do NOT guess from memory. "
        "Safe — no side-effect, runs immediately."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "required": ["meeting_id", "query"],
        "properties": {
            "meeting_id": {"type": "string", "format": "uuid"},
            "query": {"type": "string", "description": "What to look for"},
            "top_k": {"type": "integer", "description": "Max chunks (default 5)"},
        },
    },
)
async def retrieve(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Safe read tool — hybrid retrieval over a meeting's memory_events
    (bge-m3 vector + tsvector keyword + RRF via memory_service). Falls back to
    the meeting's MoM text when no embeddings exist for it yet."""
    meeting_id_str = args.get("meeting_id", "")
    query = args.get("query", "")
    if not meeting_id_str:
        return {"error": "meeting_id required"}
    try:
        mid = uuid.UUID(meeting_id_str)
    except ValueError:
        return {"error": f"invalid meeting_id: {meeting_id_str}"}

    top_k = args.get("top_k") or DEFAULT_RETRIEVE_K
    # Resolve via the package namespace so tests can monkeypatch
    # `tools.get_memory_service`. Lazy import avoids an import cycle at load.
    from src.services import tools as _tools_pkg

    svc = _tools_pkg.get_memory_service()
    events = await svc.retrieve(
        query=query, top_k=top_k, db_session=session, user_id=user_id, meeting_id=mid
    )
    if events:
        chunks = [
            {"text": e.text, "type": e.event_type, "speaker": e.speaker, "topic": e.topic}
            for e in events
        ]
        return {
            "status": "ok", "source": "memory", "query": query,
            "chunks": chunks, "count": len(chunks),
        }

    # Fallback: MoM text for this meeting (no embeddings populated).
    meeting = await repo.get_meeting(session, mid)
    mom_chunks = _mom_to_chunks(meeting) if meeting else []
    if mom_chunks:
        return {
            "status": "ok", "source": "mom", "query": query,
            "chunks": mom_chunks, "count": len(mom_chunks),
        }
    return {"status": "ok", "source": "empty", "query": query, "chunks": [], "count": 0}
