"""
MoM Generation Graph — LangGraph StateGraph với 4 nodes.

Scope: PER-RECORDING (one MoM per phiên ghi âm).
Project-level summary is a separate graph/service (project_summarizer).

Flow:
    load_transcript → read_memory → generate_mom → save_results → END

State passing:
    Mỗi node nhận state, trả về partial update dict. LangGraph tự merge.

Checkpointing:
    Compiled với AsyncPostgresSaver. thread_id = recording_id → resume per
    recording (re-run cùng recording_id sẽ resume từ node fail).

Memory context:
    read_memory dùng MemoryService (hybrid retrieval) trả past events related
    to current meeting's topic, excluding this meeting itself.

Usage:
    from src.graphs import run_mom_graph
    result = await run_mom_graph(recording_id, session, output_dir)
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.note_generator import generate_meeting_notes
from src.report_generator import generate_mom_markdown
from src.services import MemoryService, get_memory_service
from src.services.memory_service import MemoryEvent
from src.services.memory_sync_runner import schedule_project_sync

logger = logging.getLogger(__name__)


# ─── State definition ────────────────────────────────────────────

class MomState(TypedDict, total=False):
    """
    State đi xuyên qua graph. `total=False` cho phép field optional.

    Convention:
        - Input: recording_id (required), output_dir (optional)
        - Filled by load_transcript: meeting_id, transcript, meeting_meta
        - Filled by read_memory: memory_context
        - Filled by generate_mom: mom_json
        - Filled by save_results: saved_paths
    """
    # ── Input ──
    recording_id: str
    output_dir: str
    # MoM output language ("vi" / "en"). Resolved by run_mom_graph from
    # recording.mom_language → meeting.mom_language → request body → "vi".
    mom_language: str

    # ── Filled by load_transcript ──
    meeting_id: str
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
        """Node 1: SELECT segments của 1 recording, join thành transcript."""
        recording_id = state["recording_id"]
        logger.info(f"[Node load_transcript] recording_id={recording_id}")

        recording = await repo.get_recording(session, uuid.UUID(recording_id))
        if not recording:
            return {"error": f"Recording {recording_id} not found"}

        meeting = await repo.get_meeting(session, recording.meeting_id)
        if not meeting:
            return {"error": f"Parent meeting {recording.meeting_id} not found"}

        # Transcript source priority (best → worst):
        #   1. User-edited clean (TipTap output) — curated by the user
        #   2. LLM-cleaned segments with speaker labels — already attributed,
        #      filler-stripped, and structured. Big quality bump over raw.
        #   3. Raw joined segments from DB — Whisper output with no cleanup.
        clean = recording.clean_segments or {}
        clean_segs = clean.get("segments") or []

        # Race-condition guard: when the user clicks Generate MoM while the
        # background cleaner is still running on this recording, clean_segments
        # is null → we'd otherwise fall through to raw segments (no
        # cluster_mapping → MoM cites "SPEAKER_NN" instead of names). Wait
        # for the cleaner if it's in-flight, then re-fetch.
        if not clean_segs:
            try:
                from src.services.clean_orchestrator import (
                    is_inflight, wait_for_inflight,
                )
                if is_inflight(recording_id):
                    logger.info(
                        f"[Node load_transcript] cleaner is in-flight, "
                        f"waiting up to 5 min before reading transcript"
                    )
                    await wait_for_inflight(recording_id, timeout_s=300)
                    await session.refresh(recording)
                    clean = recording.clean_segments or {}
                    clean_segs = clean.get("segments") or []
                    logger.info(
                        f"[Node load_transcript] cleaner done, "
                        f"got {len(clean_segs)} clean segments"
                    )
            except Exception as e:
                logger.warning(
                    f"[Node load_transcript] wait_for_cleaner failed "
                    f"(non-fatal): {e}"
                )

        edited = (clean.get("edited_text") or "").strip()
        # cluster_mapping maps raw pyannote cluster id ("SPEAKER_00") →
        # human name ("Duy Anh"). Populated by the cleaner LLM (context
        # inference) + by user save in SpeakerMapper (voiceprint bind).
        # Apply BEFORE passing to MoM LLM so attribution lands on real
        # names — without this, MoM output cites raw "SPEAKER_00" labels
        # which is the bug users see after renaming.
        cluster_mapping = clean.get("cluster_mapping") or {}

        def _resolve_speaker(raw: str) -> str:
            if not raw:
                return "Unknown"
            mapped = cluster_mapping.get(raw)
            # Empty string / "Unknown" mapping → fall back to raw cluster id
            # so the LLM still has SOME label for that turn.
            return mapped if mapped and mapped != "Unknown" else raw

        if edited:
            # Apply cluster_mapping to edited_text too — TipTap export may
            # carry raw "SPEAKER_NN: text" labels even when the rendered UI
            # showed mapped names (depends on how the editor serialises).
            # Regex-replace each cluster id at line-start to its mapped name.
            transcript = edited
            if cluster_mapping:
                import re as _re
                for raw_id, name in cluster_mapping.items():
                    if not name or name == "Unknown":
                        continue
                    # "SPEAKER_00: ..." → "Huyền: ..." (anchor to line start
                    # + literal colon so we don't replace mid-sentence).
                    transcript = _re.sub(
                        rf"(^|\n){_re.escape(raw_id)}\s*:",
                        rf"\1{name}:",
                        transcript,
                    )
            logger.info(
                f"[Node load_transcript] using user-edited clean "
                f"({len(edited)} chars, applied {len(cluster_mapping)} mappings)"
            )
        elif clean_segs:
            # Assemble "Speaker: text" lines from the LLM-cleaned segments.
            # MoM-gen LLM finds attribution + decisions + action items much
            # easier from this than from raw.
            transcript = "\n".join(
                f"{_resolve_speaker(seg.get('speaker') or '')}: "
                f"{seg.get('text', '').strip()}"
                for seg in clean_segs
                if seg.get("text", "").strip()
            )
            logger.info(
                f"[Node load_transcript] using LLM-cleaned segments "
                f"({len(clean_segs)} blocks, {len(transcript)} chars, "
                f"cluster_mapping={cluster_mapping})"
            )
        else:
            transcript = await repo.join_recording_transcript(
                session, uuid.UUID(recording_id)
            )
            logger.info(f"[Node load_transcript] using raw segments ({len(transcript)} chars)")
        if not transcript.strip():
            return {"error": "No transcript segments for this recording"}

        logger.info(f"[Node load_transcript] loaded {len(transcript)} chars")

        # Pack meta for downstream nodes. Per-meeting-event fields live on
        # recording now (migration 0012). Project (meeting) keeps only title +
        # vocab. recording.title overrides session_label if set.
        attendees_str = ""
        if recording.attendees:
            attendees_str = ", ".join(
                f"{a.get('name', '')} ({a.get('department', '')})".strip()
                for a in recording.attendees if isinstance(a, dict)
            )
        rec_label = recording.title or recording.session_label or "Phiên họp"
        title = f"{meeting.title} — {rec_label}" if meeting.title else rec_label
        meta = {
            "title": title,
            "project_title": meeting.title,
            "session_label": rec_label,
            "purpose": recording.purpose or "",
            "date": (
                recording.date.isoformat() if recording.date
                else (recording.started_at.isoformat() if recording.started_at else "")
            ),
            "chaired_by": recording.chaired_by or "",
            "noted_by": recording.noted_by or "Mee Agent",
            "venue": recording.venue or "",
            "attendees": attendees_str,
            "topic": meeting.topic or "",
        }
        return {
            "meeting_id": str(meeting.id),
            "transcript": transcript,
            "meeting_meta": meta,
        }

    return load_transcript


def make_read_memory(memory_service: MemoryService, session: AsyncSession):
    """Factory: bind MemoryService + DB session for real retrieval."""

    async def read_memory(state: MomState) -> dict:
        """Node 2: Fetch past events related to meeting's topic (DB-backed)."""
        meta = state.get("meeting_meta", {})
        query = meta.get("topic") or meta.get("project_title") or meta.get("title", "")
        meeting_id = state.get("meeting_id")
        logger.info(f"[Node read_memory] query={query[:80]!r}")

        user = await repo.get_or_create_dev_user(session)

        events = await memory_service.retrieve(
            query=query,
            top_k=5,
            db_session=session,
            user_id=user.id,
            exclude_meeting_id=uuid.UUID(meeting_id) if meeting_id else None,
        )
        serialized = [
            {"topic": e.topic, "text": e.text, "speaker": e.speaker}
            for e in events
        ]
        logger.info(f"[Node read_memory] retrieved {len(serialized)} events")
        return {"memory_context": serialized}

    return read_memory


async def generate_mom(state: MomState) -> dict:
    """Node 3: LLM call với transcript + memory_context.

    CRITICAL: generate_meeting_notes uses the OpenAI SDK *synchronously*
    (it's not async). Calling it directly here would block FastAPI's event
    loop for the entire 1-3 minute LLM run — every concurrent request (other
    users, clean-status polls, sidebar fetches, even just clicking another
    project) would pile up pending and the UI would freeze. Wrap in
    asyncio.to_thread so the blocking call runs in the thread pool while
    the event loop stays free to serve other requests.
    """
    import asyncio as _aio
    meta = state.get("meeting_meta", {})
    logger.info(
        f"[Node generate_mom] title={meta.get('title')!r}, "
        f"transcript_len={len(state.get('transcript', ''))}, "
        f"memory_events={len(state.get('memory_context', []))}"
    )

    # MoM language resolver: recording → meeting → request body → "vi"
    # (the state's "mom_language" is set by run_mom_graph caller).
    lang = state.get("mom_language") or "vi"
    notes = await _aio.to_thread(
        generate_meeting_notes,
        transcript=state["transcript"],
        title=meta.get("title", ""),
        purpose=meta.get("purpose", ""),
        date=meta.get("date", ""),
        chaired_by=meta.get("chaired_by", ""),
        noted_by=meta.get("noted_by", "Mee Agent"),
        venue=meta.get("venue", ""),
        attendees=meta.get("attendees", ""),
        lang=lang,
    )

    if "error" in notes:
        return {"error": notes["error"]}
    return {"mom_json": notes, "error": None}


def make_save_results(session: AsyncSession, memory_service: MemoryService):
    """Factory: bind session + memory."""

    async def save_results(state: MomState) -> dict:
        """Node 4: Save mom_json to recording.mom_json + write .md + extract memory events."""
        recording_id = state["recording_id"]
        meeting_id = state.get("meeting_id")
        mom = state.get("mom_json", {})
        output_dir = state.get("output_dir", "output")
        meta = state.get("meeting_meta", {})
        topic = meta.get("topic") or mom.get("title", "")

        # 1. Save MoM JSON to the RECORDING row (per-recording MoM)
        await repo.save_recording_mom(session, uuid.UUID(recording_id), mom)

        # 2. Generate .md file — filename uses session_label so files are
        # distinguishable across recordings of the same project.
        md_path = generate_mom_markdown(
            notes=mom,
            output_dir=output_dir,
            recording_label=meta.get("session_label"),
        )
        logger.info(f"[Node save_results] saved md → {md_path}")

        # 3. Extract structured events from MoM → memory_events table.
        # Events are tagged with meeting_id (project-level) so cross-recording
        # memory retrieval still works.
        user = await repo.get_or_create_dev_user(session)
        events: list[MemoryEvent] = []

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

        summary = mom.get("summary")
        if summary:
            events.append(MemoryEvent(
                meeting_id=meeting_id, topic=topic,
                event_type="summary", text=summary,
            ))

        saved_count = 0
        if events:
            await memory_service.save(
                events,
                db_session=session,
                user_id=user.id,
                meeting_id=uuid.UUID(meeting_id),
            )
            saved_count = len(events)

        # Event-driven AgentBase re-sync: this recording's MoM just changed the
        # project's state. Fire-and-forget (own session, best-effort) so it never
        # blocks or breaks MoM generation. No-op if MEMORY_ID is unset.
        if meeting_id:
            schedule_project_sync(meeting_id)

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
    g = StateGraph(MomState)

    g.add_node("load_transcript", make_load_transcript(session))
    g.add_node("read_memory", make_read_memory(memory_service, session))
    g.add_node("generate_mom", generate_mom)
    g.add_node("save_results", make_save_results(session, memory_service))

    def _route_after_load(state: MomState) -> str:
        if state.get("error") and not state.get("transcript"):
            return END
        return "read_memory"

    g.set_entry_point("load_transcript")
    g.add_conditional_edges(
        "load_transcript", _route_after_load,
        {END: END, "read_memory": "read_memory"},
    )
    g.add_edge("read_memory", "generate_mom")
    g.add_edge("generate_mom", "save_results")
    g.add_edge("save_results", END)

    return g.compile(checkpointer=checkpointer)


# ─── High-level runner (used by endpoint) ────────────────────────

async def run_mom_graph(
    recording_id: str,
    session: AsyncSession,
    output_dir: str = "output",
    checkpointer=None,
    memory_service: Optional[MemoryService] = None,
    mom_language: Optional[str] = None,
) -> MomState:
    """
    Invoke the graph for 1 recording. thread_id = recording_id → per-recording resume.

    `mom_language`: ui_lang fallback when neither recording nor meeting has
    a value set. Resolved here so generate_mom node just reads state.
    """
    if memory_service is None:
        memory_service = get_memory_service()

    # Resolver: recording.mom_language → meeting.mom_language → caller hint → "vi".
    # We need the DB to know recording/meeting values — fetch quickly here.
    import uuid as _uuid
    from src.db import repositories as repo
    try:
        rid = _uuid.UUID(recording_id)
        rec = await repo.get_recording(session, rid)
        if rec:
            mt = await repo.get_meeting(session, rec.meeting_id)
            resolved_lang = (
                (rec.mom_language or "").strip()
                or (mt.mom_language if mt else None and mt.mom_language.strip())
                or (mom_language or "vi")
            )
        else:
            resolved_lang = mom_language or "vi"
    except Exception:
        resolved_lang = mom_language or "vi"

    graph = build_mom_graph(session, memory_service, checkpointer)

    config = {"configurable": {"thread_id": recording_id}}
    initial_state: MomState = {
        "recording_id": recording_id,
        "output_dir": output_dir,
        "mom_language": resolved_lang,
    }

    logger.info(f"=== Running MomGraph for recording {recording_id} ===")
    result: MomState = await graph.ainvoke(initial_state, config=config)
    logger.info(f"=== MomGraph done. saved_paths={result.get('saved_paths')} ===")
    return result
