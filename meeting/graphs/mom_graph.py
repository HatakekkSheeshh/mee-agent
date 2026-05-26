"""
MoM Generation Graph — LangGraph StateGraph với 4 nodes.

Flow:
    load_transcript → read_memory → generate_mom → save_results → END

State passing:
    Mỗi node nhận state, trả về partial update dict. LangGraph tự merge.

Checkpointing:
    Compiled với AsyncPostgresSaver. Mỗi node xong → state save. Fail ở node 3
    → re-invoke với same thread_id → resume từ node 3 (skip node 1+2).

Memory context (Phase B mock):
    read_memory dùng MemoryService stub trả [] cho mock. Phase F sẽ wire real.

Usage:
    from meeting.graphs import run_mom_graph
    result = await run_mom_graph(meeting_id, session, output_dir)
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.note_generator import generate_meeting_notes
from meeting.report_generator import generate_mom_markdown
from meeting.services import MemoryService, get_memory_service
from meeting.services.memory_service import MemoryEvent

logger = logging.getLogger(__name__)


# ─── State definition ────────────────────────────────────────────

class MomState(TypedDict, total=False):
    """
    State đi xuyên qua graph. `total=False` cho phép field optional.

    Convention:
        - Input fields: required khi invoke (meeting_id, output_dir)
        - Output fields: filled by nodes
    """
    # ── Input (provided when invoking graph) ──
    meeting_id: str
    output_dir: str

    # ── Filled by load_transcript ──
    transcript: str
    meeting_meta: dict          # title, purpose, attendees, etc.

    # ── Filled by read_memory ──
    memory_context: list[dict]  # past events related to this meeting's topic

    # ── Filled by generate_mom ──
    mom_json: dict

    # ── Filled by save_results ──
    saved_paths: dict           # {"md": "...", "db": True}

    # ── Error tracking (optional) ──
    error: Optional[str]


# ─── Node functions ──────────────────────────────────────────────

def make_load_transcript(session: AsyncSession):
    """Factory: bind AsyncSession into node closure."""

    async def load_transcript(state: MomState) -> dict:
        """Node 1: SELECT segments từ DB, COALESCE(edited, original), join."""
        meeting_id = state["meeting_id"]
        logger.info(f"[Node load_transcript] meeting_id={meeting_id}")

        meeting = await repo.get_meeting(session, uuid.UUID(meeting_id))
        if not meeting:
            return {"error": f"Meeting {meeting_id} not found"}

        transcript = await repo.join_meeting_transcript(
            session, uuid.UUID(meeting_id)
        )
        if not transcript.strip():
            return {"error": "No transcript segments for this meeting"}

        logger.info(f"[Node load_transcript] loaded {len(transcript)} chars")

        # Pack meta for downstream nodes
        attendees_str = ""
        if meeting.attendees:
            attendees_str = ", ".join(
                f"{a.get('name', '')} ({a.get('department', '')})".strip()
                for a in meeting.attendees if isinstance(a, dict)
            )
        meta = {
            "title": meeting.title,
            "purpose": meeting.purpose or "",
            "date": meeting.date.isoformat() if meeting.date else "",
            "chaired_by": meeting.chaired_by or "",
            "noted_by": meeting.noted_by or "Mee Agent",
            "venue": meeting.venue or "",
            "attendees": attendees_str,
            "topic": meeting.topic or "",
        }
        return {"transcript": transcript, "meeting_meta": meta}

    return load_transcript


def make_read_memory(memory_service: MemoryService, session: AsyncSession):
    """Factory: bind MemoryService + DB session for real retrieval."""

    async def read_memory(state: MomState) -> dict:
        """Node 2: Fetch past events related to meeting's topic (DB-backed)."""
        meta = state.get("meeting_meta", {})
        query = meta.get("topic") or meta.get("title", "")
        meeting_id = state.get("meeting_id")
        logger.info(f"[Node read_memory] query={query[:80]!r}")

        # Get current user (dev user for now until M365 auth)
        user = await repo.get_or_create_dev_user(session)

        events = await memory_service.retrieve(
            query=query,
            top_k=5,
            db_session=session,
            user_id=user.id,
            exclude_meeting_id=uuid.UUID(meeting_id) if meeting_id else None,
        )
        # Serialize MemoryEvent → dict for state (TypedDict prefers plain dicts)
        serialized = [
            {"topic": e.topic, "text": e.text, "speaker": e.speaker}
            for e in events
        ]
        logger.info(f"[Node read_memory] retrieved {len(serialized)} events")
        return {"memory_context": serialized}

    return read_memory


async def generate_mom(state: MomState) -> dict:
    """Node 3: LLM call với transcript + memory_context."""
    meta = state.get("meeting_meta", {})
    logger.info(
        f"[Node generate_mom] title={meta.get('title')!r}, "
        f"transcript_len={len(state.get('transcript', ''))}, "
        f"memory_events={len(state.get('memory_context', []))}"
    )

    notes = generate_meeting_notes(
        transcript=state["transcript"],
        title=meta.get("title", ""),
        purpose=meta.get("purpose", ""),
        date=meta.get("date", ""),
        chaired_by=meta.get("chaired_by", ""),
        noted_by=meta.get("noted_by", "Mee Agent"),
        venue=meta.get("venue", ""),
        attendees=meta.get("attendees", ""),
    )

    if "error" in notes:
        return {"error": notes["error"]}
    return {"mom_json": notes}


def make_save_results(session: AsyncSession, memory_service: MemoryService):
    """Factory: bind session + memory."""

    async def save_results(state: MomState) -> dict:
        """Node 4: Save mom_json to DB + write .md file + save memory events (DB-backed)."""
        meeting_id = state["meeting_id"]
        mom = state.get("mom_json", {})
        output_dir = state.get("output_dir", "output")
        topic = state.get("meeting_meta", {}).get("topic") or mom.get("title", "")

        # 1. Save MoM JSON to DB
        await repo.save_mom(session, uuid.UUID(meeting_id), mom)

        # 2. Generate .md file
        md_path = generate_mom_markdown(notes=mom, output_dir=output_dir)
        logger.info(f"[Node save_results] saved md → {md_path}")

        # 3. Extract structured events from MoM → memory_events table
        user = await repo.get_or_create_dev_user(session)
        events: list[MemoryEvent] = []

        # 3a. Action items → 'action_item' events with PIC + deadline
        for ai in mom.get("action_items", []) or []:
            if ai.get("item"):
                events.append(MemoryEvent(
                    meeting_id=meeting_id,
                    topic=topic,
                    event_type="action_item",
                    text=ai["item"],
                    speaker=ai.get("pic"),
                    deadline=ai.get("deadline"),
                ))

        # 3b. Decisions (from new prompt extraction — Sprint D)
        for dec in mom.get("decisions", []) or []:
            if isinstance(dec, str) and dec.strip():
                events.append(MemoryEvent(
                    meeting_id=meeting_id, topic=topic,
                    event_type="decision", text=dec.strip(),
                ))
            elif isinstance(dec, dict) and dec.get("text"):
                events.append(MemoryEvent(
                    meeting_id=meeting_id, topic=topic,
                    event_type="decision", text=dec["text"],
                    speaker=dec.get("by"),
                ))

        # 3c. Commitments
        for c in mom.get("commitments", []) or []:
            if isinstance(c, str) and c.strip():
                events.append(MemoryEvent(
                    meeting_id=meeting_id, topic=topic,
                    event_type="commitment", text=c.strip(),
                ))
            elif isinstance(c, dict) and c.get("text"):
                events.append(MemoryEvent(
                    meeting_id=meeting_id, topic=topic,
                    event_type="commitment", text=c["text"],
                    speaker=c.get("by"),
                ))

        # 3d. Blockers
        for b in mom.get("blockers", []) or []:
            if isinstance(b, str) and b.strip():
                events.append(MemoryEvent(
                    meeting_id=meeting_id, topic=topic,
                    event_type="blocker", text=b.strip(),
                ))
            elif isinstance(b, dict) and b.get("text"):
                events.append(MemoryEvent(
                    meeting_id=meeting_id, topic=topic,
                    event_type="blocker", text=b["text"],
                    speaker=b.get("by"),
                ))

        # 3e. Summary
        summary = mom.get("summary")
        if summary:
            events.append(MemoryEvent(
                meeting_id=meeting_id, topic=topic,
                event_type="summary", text=summary,
            ))

        # Save all events to DB
        saved_count = 0
        if events:
            await memory_service.save(
                events,
                db_session=session,
                user_id=user.id,
                meeting_id=uuid.UUID(meeting_id),
            )
            saved_count = len(events)

        return {
            "saved_paths": {
                "md": md_path,
                "db": True,
                "memory_events": saved_count,
            }
        }

    return save_results


# ─── Graph builder ───────────────────────────────────────────────

def build_mom_graph(
    session: AsyncSession,
    memory_service: MemoryService,
    checkpointer=None,
):
    """
    Build + compile MomGraph.

    Args:
        session: SQLAlchemy AsyncSession (request-scoped)
        memory_service: stub now, real later
        checkpointer: AsyncPostgresSaver or None (no resume support)
    """
    g = StateGraph(MomState)

    g.add_node("load_transcript", make_load_transcript(session))
    g.add_node("read_memory", make_read_memory(memory_service, session))
    g.add_node("generate_mom", generate_mom)
    g.add_node("save_results", make_save_results(session, memory_service))

    g.set_entry_point("load_transcript")
    g.add_edge("load_transcript", "read_memory")
    g.add_edge("read_memory", "generate_mom")
    g.add_edge("generate_mom", "save_results")
    g.add_edge("save_results", END)

    return g.compile(checkpointer=checkpointer)


# ─── High-level runner (used by endpoint) ────────────────────────

async def run_mom_graph(
    meeting_id: str,
    session: AsyncSession,
    output_dir: str = "output",
    checkpointer=None,
    memory_service: Optional[MemoryService] = None,
) -> MomState:
    """
    Invoke the graph and return final state.

    thread_id = meeting_id → resume works if invoked again with same meeting.
    """
    if memory_service is None:
        memory_service = get_memory_service()

    graph = build_mom_graph(session, memory_service, checkpointer)

    config = {"configurable": {"thread_id": meeting_id}}
    initial_state: MomState = {
        "meeting_id": meeting_id,
        "output_dir": output_dir,
    }

    logger.info(f"=== Running MomGraph for meeting {meeting_id} ===")
    result: MomState = await graph.ainvoke(initial_state, config=config)
    logger.info(f"=== MomGraph done. saved_paths={result.get('saved_paths')} ===")
    return result
