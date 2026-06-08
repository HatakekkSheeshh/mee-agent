"""
Tool Registry — actions Mee agent can request HITL approval to execute.

Pattern:
    Each tool is a dict with:
        name           — unique id, LLM uses this
        description    — what it does (LLM reads this to decide when to call)
        side_effect    — True if needs HITL approval; False = safe to auto-run
        schema         — JSON Schema for args validation
        executor       — async function to actually run

Tools:
    - retrieve       — safe: hybrid retrieval over a meeting (memory_service + MoM)
    - search_transcript — safe: keyword match over a meeting's transcript
    - switch_meeting — safe: resolve a project by title (re-scopes retrieval)
    - create_task    — side-effect: builds a structured task from MoM action_items
    - send_email     — side-effect, still MOCK (Phase E wires MS Graph)

Adding new tool:
    1. Define spec dict
    2. Add to TOOLS registry
    3. LLM tự pick lên qua /describe endpoint
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services.memory_service import get_memory_service

logger = logging.getLogger(__name__)

# Default number of retrieved chunks for the `retrieve` tool.
DEFAULT_RETRIEVE_K = 5


# ─── Tool executors (mock) ────────────────────────────────────────

async def _exec_send_email(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """MOCK: simulate sending email. Phase E will wire MS Graph."""
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    logger.info(f"[MOCK send_email] to={to!r} subject={subject!r} body_len={len(body)}")
    return {
        "status": "sent_mock",
        "to": to,
        "subject": subject,
        "message_id": f"mock-{uuid.uuid4().hex[:8]}",
        "note": "Mock execution — Phase E wires MS Graph for real send.",
    }


def build_task_items(action_items: list[dict]) -> list[dict]:
    """Normalize MoM action_items ({pic, deadline, item}) → task items
    ({subject, assignee, due_date, description}). Drops items without text."""
    return [
        {
            "subject": ai.get("item", ""),
            "assignee": ai.get("pic", ""),
            "due_date": ai.get("deadline", ""),
            "description": "",
        }
        for ai in action_items
        if ai.get("item")
    ]


async def _exec_create_task(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Build a structured task from explicit args OR the meeting's MoM action_items.

    Produces a normalized task list ({subject, assignee, due_date, description}).
    No external write yet — persistence / pm-agent reconcile is a later step; this
    is the HITL-approved producer the chat agent surfaces for confirmation.
    """
    explicit_title = args.get("title") or args.get("subject")
    if explicit_title:
        task = {
            "subject": explicit_title,
            "assignee": args.get("assignee", ""),
            "due_date": args.get("deadline") or args.get("due_date", ""),
            "description": args.get("description", ""),
        }
        logger.info(f"[create_task] explicit task subject={explicit_title!r}")
        return {"status": "prepared", "source": "explicit", "tasks": [task], "count": 1}

    meeting_id_str = args.get("meeting_id", "")
    if not meeting_id_str:
        return {"error": "create_task needs a title, or a meeting_id with action_items"}
    try:
        mid = uuid.UUID(meeting_id_str)
    except ValueError:
        return {"error": f"invalid meeting_id: {meeting_id_str}"}

    action_items = await repo.get_mom_action_items(session, mid)
    tasks = build_task_items(action_items)
    if not tasks:
        return {"error": "no action_items found in this meeting's MoM"}
    logger.info(f"[create_task] built {len(tasks)} task(s) from MoM action_items")
    return {"status": "prepared", "source": "mom", "tasks": tasks, "count": len(tasks)}


async def _exec_search_transcript(
    args: dict, *, session: AsyncSession, user_id: uuid.UUID
) -> dict:
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


async def _exec_retrieve(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
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
    svc = get_memory_service()
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


async def _exec_switch_meeting(
    args: dict, *, session: AsyncSession, user_id: uuid.UUID
) -> dict:
    """Safe read tool — resolve a project the user names by title and return its
    id (most-recent match) so the agent can re-scope subsequent retrieval."""
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    matches = await repo.find_meetings_by_title(session, user_id, title)
    if not matches:
        return {"status": "not_found", "title": title, "candidates": []}
    return {
        "status": "ok",
        "meeting_id": str(matches[0].id),
        "candidates": [{"id": str(m.id), "title": m.title} for m in matches],
    }


async def _exec_list_recordings(
    args: dict, *, session: AsyncSession, user_id: uuid.UUID
) -> dict:
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


async def _exec_recording_mom(
    args: dict, *, session: AsyncSession, user_id: uuid.UUID
) -> dict:
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


# ─── Tool registry ────────────────────────────────────────────────

TOOLS: dict[str, dict[str, Any]] = {
    "send_email": {
        "name": "send_email",
        "description": (
            "Send an email to one or more recipients. "
            "Use when user explicitly asks to email someone the MoM, summary, action items, etc. "
            "REQUIRES user approval before execution (side-effect)."
        ),
        "side_effect": True,
        "schema": {
            "type": "object",
            "required": ["to", "subject", "body"],
            "properties": {
                "to": {"type": "string", "description": "Comma-separated recipients"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths to attach",
                },
            },
        },
        "executor": _exec_send_email,
    },
    "create_task": {
        "name": "create_task",
        "description": (
            "Prepare a task (or batch of tasks) to track action items. "
            "Provide an explicit `title` (+ optional assignee/deadline) for a single "
            "task, OR pass only `meeting_id` to build the task list automatically from "
            "the meeting's MoM action_items. "
            "REQUIRES user approval before execution (side-effect)."
        ),
        "side_effect": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Single-task subject"},
                "assignee": {"type": "string", "description": "Name or email"},
                "deadline": {"type": "string", "description": "Date, e.g. DD/MM/YYYY"},
                "description": {"type": "string"},
                "meeting_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Build tasks from this meeting's MoM action_items",
                },
            },
        },
        "executor": _exec_create_task,
    },
    "switch_meeting": {
        "name": "switch_meeting",
        "description": (
            "Switch the active project/meeting by title when the user asks about a "
            "DIFFERENT project than the current one. Returns the matched meeting(s). "
            "After switching, use `retrieve` to read that project's content. "
            "Safe — no side-effect, runs immediately."
        ),
        "side_effect": False,
        "schema": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "description": "Project/meeting title fragment"},
            },
        },
        "executor": _exec_switch_meeting,
    },
    "retrieve": {
        "name": "retrieve",
        "description": (
            "Search the current meeting/project's minutes (MoM) and transcript "
            "for content relevant to a query. Call this FIRST whenever you need "
            "meeting content to answer a question — do NOT guess from memory. "
            "Safe — no side-effect, runs immediately."
        ),
        "side_effect": False,
        "schema": {
            "type": "object",
            "required": ["meeting_id", "query"],
            "properties": {
                "meeting_id": {"type": "string", "format": "uuid"},
                "query": {"type": "string", "description": "What to look for"},
                "top_k": {"type": "integer", "description": "Max chunks (default 5)"},
            },
        },
        "executor": _exec_retrieve,
    },
    "list_recordings": {
        "name": "list_recordings",
        "description": (
            "List the recordings (phiên/buổi họp) of the current project, with "
            "each recording's label, date, and whether it has minutes (MoM). "
            "Call this FIRST when the user asks about a SPECIFIC recording / "
            "phiên / 'Meeting N' / a date, to resolve which recording they mean "
            "before reading it with `recording_mom`. "
            "Safe — no side-effect, runs immediately."
        ),
        "side_effect": False,
        "schema": {
            "type": "object",
            "properties": {
                "meeting_id": {"type": "string", "format": "uuid"},
            },
        },
        "executor": _exec_list_recordings,
    },
    "recording_mom": {
        "name": "recording_mom",
        "description": (
            "Read ONE recording's minutes (MoM): summary, decisions, and "
            "action_items (each with `pic`/người phụ trách, deadline, item). "
            "Use after `list_recordings` to answer questions scoped to a "
            "specific recording — e.g. 'việc của Hiếu trong phiên X' — by reading "
            "that recording's action_items and filtering by `pic`. Only attribute "
            "a fact to the recording you actually read. "
            "Safe — no side-effect, runs immediately."
        ),
        "side_effect": False,
        "schema": {
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
        "executor": _exec_recording_mom,
    },
    "search_transcript": {
        "name": "search_transcript",
        "description": (
            "Search within a meeting's transcript for keyword matches. "
            "Use when user asks 'when did X say Y' or 'did anyone mention Z'. "
            "Safe — no side-effect, runs immediately."
        ),
        "side_effect": False,
        "schema": {
            "type": "object",
            "required": ["meeting_id", "query"],
            "properties": {
                "meeting_id": {"type": "string", "format": "uuid"},
                "query": {"type": "string"},
            },
        },
        "executor": _exec_search_transcript,
    },
}


def list_tools() -> list[dict]:
    """Return tool specs for LLM prompt (without executor)."""
    return [
        {k: v for k, v in spec.items() if k != "executor"}
        for spec in TOOLS.values()
    ]


def get_tool(name: str) -> Optional[dict]:
    return TOOLS.get(name)


async def execute_tool(
    name: str,
    args: dict,
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
) -> dict:
    """Run tool by name. Audit-logged."""
    tool = TOOLS.get(name)
    if not tool:
        raise ValueError(f"Unknown tool: {name}")
    executor = tool["executor"]
    try:
        result = await executor(args, session=session, user_id=user_id)
        await repo.log_audit(
            session,
            user_id=user_id,
            session_id=None,  # caller passes session_id via wrapper
            action_type="tool_execute",
            tool_name=name,
            tool_args=args,
            result=result,
            success=True,
        )
        return result
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        await repo.log_audit(
            session,
            user_id=user_id,
            session_id=None,
            action_type="tool_execute",
            tool_name=name,
            tool_args=args,
            success=False,
            error_msg=str(e),
        )
        return {"error": str(e), "tool": name}
