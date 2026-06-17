"""Context + persistence nodes for the chat graph (load_context, save_reply) +
meeting resolution."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.db.models import User
from src.graphs._chat_state import ChatState
from src.memory_client import (
    STALE_NOTE,
    fact_namespace,
    is_record_stale,
    list_fact_records,
    search_project_record,
    strip_project_marker,
)
from src.services.meeting_resolver import default_generate, llm_resolve_meeting
from src.services.memory_sync import canonical_source_hash
from src.services.memory_sync_runner import schedule_project_sync

logger = logging.getLogger(__name__)

# Cap how many remembered facts are injected into the prompt so context doesn't
# bloat as memory grows. list_fact_records returns newest-first, so we keep the
# N most recent.
MAX_RECALLED_FACTS = 20


async def resolve_meeting(
    session: AsyncSession,
    *,
    user_id,
    bound_meeting_id: Optional[str],
    title: Optional[str],
    generate=None,
) -> dict:
    """Resolve which meeting the user means.

    Default = the chat's bound meeting_id. If a `title` is named:
      - ILIKE fast-path: exactly 1 match → use it (no LLM).
      - 0 or >1 matches → LLM-resolve over the user's meetings by title
        (handles acronyms/abbreviations ILIKE can't, e.g. "GIP").
      - LLM finds nothing → fall back to bound, but return the user's meetings as
        `candidates` so the agent can offer near-matches instead of creating new.

    Returns {meeting_id, resolved_by: "bound"|"title", candidates: [{id,title}]}.
    """
    if not (title and title.strip()):
        return {"meeting_id": bound_meeting_id, "resolved_by": "bound", "candidates": []}

    matches = await repo.find_meetings_by_title(session, user_id, title)
    if len(matches) == 1:
        m = matches[0]
        return {
            "meeting_id": str(m.id),
            "resolved_by": "title",
            "candidates": [{"id": str(m.id), "title": m.title}],
        }

    # Ambiguous (0 or >1): LLM-resolve over ALL the user's meetings.
    candidates = await repo.list_meetings_for_user(session, user_id)
    candidate_dicts = [{"id": str(m.id), "title": m.title} for m in candidates]
    chosen_id = llm_resolve_meeting(
        title, candidates, generate=generate or default_generate
    ) if candidates else None
    if chosen_id:
        return {
            "meeting_id": chosen_id,
            "resolved_by": "title",
            "candidates": candidate_dicts,
        }
    # No confident match — keep the bound scope, surface near-matches.
    return {
        "meeting_id": bound_meeting_id,
        "resolved_by": "bound",
        "candidates": candidate_dicts,
    }

def make_load_context(
    session: AsyncSession, *, search_record=None, schedule_resync=None, list_facts=None
):
    # DI seams: real AgentBase browse + fire-and-forget bg re-sync by default;
    # tests inject fakes.
    _search = search_record or search_project_record
    _schedule_resync = schedule_resync or schedule_project_sync
    _list_facts = list_facts or list_fact_records

    async def load_context(state: ChatState) -> dict:
        """Load meeting context + recent messages for the LLM prompt."""
        sid = uuid.UUID(state["session_id"])
        # Recent messages (last 10)
        messages = await repo.list_chat_messages(session, sid, limit=10)
        recent = [{"role": m.role, "content": m.content} for m in messages]

        # Signed-in user identity → injected into the agent prompt so it knows who
        # "tôi/của tôi" is and can scope role-based tool calls (not just kickoff).
        user_name = user_role = user_email = None
        user_oid: str | None = None
        user_meetings: list[dict] = []
        uid_str = state.get("user_id")
        if session is not None and uid_str:
            uid = uuid.UUID(uid_str)
            user = await session.get(User, uid)
            if user:
                user_name = (user.display_name or "").strip() or None
                user_role = user.role.name if user.role else None
                user_email = (user.email or "").strip() or None
                user_oid = (getattr(user, "ms_oid", None) or "").strip() or None
            # Roster of the user's projects so the agent can recognise a name the
            # user mentions as a SEPARATE project and call switch_meeting (instead
            # of assuming it's an alias of the current meeting). Best-effort.
            try:
                roster = await repo.list_meetings_for_user(session, uid)
                user_meetings = [{"id": str(m.id), "title": m.title} for m in roster]
            except Exception as e:  # noqa: BLE001 — roster is non-critical orientation
                logger.warning(f"[Node load_context] user meetings roster failed: {e}")

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

        # Remembered facts (remember_fact): user-scoped recall happens even with no
        # meeting bound ("gọi tôi là Ronaldo" must surface in general chat too);
        # project-scoped recall is keyed by the turn's meeting. Best-effort + off the
        # event loop (sync urllib); a recall failure never blocks the turn.
        remembered: list[str] = []
        if user_oid:
            try:
                remembered += await asyncio.to_thread(
                    _list_facts, fact_namespace("user", user_oid)
                )
            except Exception as e:  # noqa: BLE001 — recall is non-critical
                logger.warning(f"[Node load_context] user fact recall failed: {e}")
        if turn_meeting_id and meeting_ctx.get("id"):
            try:
                remembered += await asyncio.to_thread(
                    _list_facts, fact_namespace("project", meeting_ctx["id"])
                )
            except Exception as e:  # noqa: BLE001 — recall is non-critical
                logger.warning(f"[Node load_context] project fact recall failed: {e}")
        if remembered:
            block = "Ghi nhớ (đã lưu từ hội thoại trước):\n" + "\n".join(
                f"- {f}" for f in remembered[:MAX_RECALLED_FACTS]
            )
            project_memory = f"{project_memory}\n\n{block}" if project_memory else block

        logger.info(
            f"[Node load_context] session={state['session_id'][:8]}, "
            f"recent_msgs={len(recent)}, meeting={meeting_ctx.get('title', 'none')!r}, "
            f"project_memory={len(project_memory)} chars"
            f"{' (recalled)' if project_memory else ' (none)'}, "
            f"facts={len(remembered)}"
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
            "user_meetings": user_meetings,
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
