"""Context + persistence nodes for the chat graph (load_context, save_reply) +
meeting resolution."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.graphs._chat_state import ChatState
from meeting.memory_client import search_project_record, strip_project_marker

logger = logging.getLogger(__name__)

async def resolve_meeting(
    session: AsyncSession,
    *,
    user_id,
    bound_meeting_id: Optional[str],
    title: Optional[str],
) -> dict:
    """Resolve which meeting the user means.

    Default = the chat's bound meeting_id. If a `title` is named, ILIKE-resolve
    the user's meetings (most-recent first) and pick the most recent match; on
    no match, fall back to the bound meeting.

    Returns {meeting_id, resolved_by: "bound"|"title", candidates: [{id,title}]}.
    """
    if title and title.strip():
        matches = await repo.find_meetings_by_title(session, user_id, title)
        if matches:
            return {
                "meeting_id": str(matches[0].id),
                "resolved_by": "title",
                "candidates": [{"id": str(m.id), "title": m.title} for m in matches],
            }
    return {"meeting_id": bound_meeting_id, "resolved_by": "bound", "candidates": []}

def make_load_context(session: AsyncSession, *, search_record=None):
    # DI seam: real AgentBase browse by default; tests inject a fake.
    _search = search_record or search_project_record

    async def load_context(state: ChatState) -> dict:
        """Load meeting context + recent messages for the LLM prompt."""
        sid = uuid.UUID(state["session_id"])
        # Recent messages (last 10)
        messages = await repo.list_chat_messages(session, sid, limit=10)
        recent = [{"role": m.role, "content": m.content} for m in messages]

        meeting_ctx = {}
        project_memory = ""
        chat_sess = await repo.get_chat_session(session, sid)
        if chat_sess and chat_sess.meeting_id:
            meeting = await repo.get_meeting(session, chat_sess.meeting_id)
            if meeting:
                # Best-effort recall of the distilled project-state projection
                # (AgentBase). Off the event loop (sync urllib); never block a turn.
                try:
                    rec = await asyncio.to_thread(_search, str(meeting.id))
                    if rec:
                        project_memory = strip_project_marker(rec.get("memory"))
                except Exception as e:  # noqa: BLE001 — recall is non-critical
                    logger.warning(f"[Node load_context] project memory recall failed: {e}")
                meeting_ctx = {
                    "id": str(meeting.id),
                    "title": meeting.title,
                    # `purpose` moved to recording in migration 0012 — chat
                    # context could aggregate per-recording purposes if needed.
                    "project_summary_json": meeting.project_summary_json,
                    "recording_moms": [
                        {"recording_id": str(r.id),
                         "session_label": r.title or r.session_label,
                         "purpose": r.purpose,
                         "mom_json": r.mom_json}
                        for r in (meeting.recordings or [])
                        if r.mom_json
                    ],
                }

        logger.info(
            f"[Node load_context] session={state['session_id'][:8]}, "
            f"recent_msgs={len(recent)}, meeting={meeting_ctx.get('title', 'none')!r}, "
            f"project_memory={len(project_memory)} chars"
            f"{' (recalled)' if project_memory else ' (none)'}"
        )
        return {
            "recent_messages": recent,
            "meeting_context": meeting_ctx,
            "project_memory": project_memory,
            # Default scope for the agent's tools = the chat's bound meeting.
            # switch_meeting can re-scope this mid-conversation by title.
            "resolved_meeting_id": meeting_ctx.get("id") or state.get("meeting_id"),
        }

    return load_context

def make_save_reply(session: AsyncSession):
    async def save_reply(state: ChatState) -> dict:
        """Persist user msg + agent reply into chat_messages."""
        sid = uuid.UUID(state["session_id"])

        # Save user message
        await repo.add_chat_message(
            session,
            session_id=sid,
            role="user",
            content={"text": state["user_message"]},
        )

        # Save agent reply
        agent_content = {"text": state.get("final_reply", "")}
        if state.get("tool_result"):
            agent_content["tool_result"] = state["tool_result"]
        tools_called = [
            tc["function"]["name"]
            for m in (state.get("agent_messages") or [])
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
        ]
        if tools_called:
            agent_content["tools_called"] = tools_called

        await repo.add_chat_message(
            session,
            session_id=sid,
            role="agent",
            content=agent_content,
            metadata={"intent": state.get("intent")},
        )
        logger.info(f"[Node save_reply] persisted 2 messages")
        return {}

    return save_reply
