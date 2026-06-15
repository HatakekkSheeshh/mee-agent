"""Context + persistence nodes for the chat graph (load_context, save_reply) +
meeting resolution."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.db.models import User
from meeting.graphs._chat_state import ChatState
from meeting.memory_client import (
    STALE_NOTE,
    is_record_stale,
    search_project_record,
    strip_project_marker,
)
from meeting.services.memory_sync import canonical_source_hash
from meeting.services.memory_sync_runner import schedule_project_sync

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

def make_load_context(session: AsyncSession, *, search_record=None, schedule_resync=None):
    # DI seams: real AgentBase browse + fire-and-forget bg re-sync by default;
    # tests inject fakes.
    _search = search_record or search_project_record
    _schedule_resync = schedule_resync or schedule_project_sync

    async def load_context(state: ChatState) -> dict:
        """Load meeting context + recent messages for the LLM prompt."""
        sid = uuid.UUID(state["session_id"])
        # Recent messages (last 10)
        messages = await repo.list_chat_messages(session, sid, limit=10)
        recent = [{"role": m.role, "content": m.content} for m in messages]

        # Signed-in user identity → injected into the agent prompt so it knows who
        # "tôi/của tôi" is and can scope role-based tool calls (not just kickoff).
        user_name = user_role = user_email = None
        uid_str = state.get("user_id")
        if session is not None and uid_str:
            user = await session.get(User, uuid.UUID(uid_str))
            if user:
                user_name = (user.display_name or "").strip() or None
                user_role = user.role.name if user.role else None
                user_email = (user.email or "").strip() or None

        meeting_ctx = {}
        project_memory = ""
        # User-scoped sessions: ground on the per-turn meeting_id (the UI-selected
        # project passed with this message), NOT a session column. None → general.
        turn_meeting_id = state.get("meeting_id")
        if turn_meeting_id:
            meeting = await repo.get_meeting(session, uuid.UUID(turn_meeting_id))
            if meeting:
                # Best-effort recall of the distilled project-state projection
                # (AgentBase). Off the event loop (sync urllib); never block a turn.
                try:
                    rec = await asyncio.to_thread(_search, str(meeting.id))
                    if rec:
                        project_memory = strip_project_marker(rec.get("memory"))
                        # Q1 staleness check: the distilled projection is a cache that
                        # can lag Postgres (a sync hook that failed, or pre-backfill
                        # data). Compare the record's marker hash to the live hash of
                        # the meeting's CURRENT data. On mismatch, flag it honestly +
                        # kick a NON-BLOCKING bg re-sync (self-heals next turn); never
                        # block this turn re-distilling.
                        recs = sorted((meeting.recordings or []), key=repo.recording_sort_key)
                        live_hash = canonical_source_hash(
                            meeting.project_summary_json, [r.mom_json for r in recs]
                        )
                        if is_record_stale(rec.get("memory"), live_hash):
                            project_memory = (
                                f"{project_memory}\n\n{STALE_NOTE}"
                                if project_memory else STALE_NOTE
                            )
                            logger.info(
                                f"[Node load_context] project memory STALE for "
                                f"{meeting.id} — flagged + bg re-sync queued"
                            )
                            try:
                                _schedule_resync(str(meeting.id))
                            except Exception as e:  # noqa: BLE001 — re-sync is best-effort
                                logger.warning(
                                    f"[Node load_context] bg re-sync schedule failed: {e}"
                                )
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
            "user_name": user_name,
            "user_role": user_role,
            "user_email": user_email,
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
