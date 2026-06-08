"""
Tool Registry — actions Mee agent can request HITL approval to execute.

Pattern:
    Each tool is a dict with:
        name           — unique id, LLM uses this
        description    — what it does (LLM reads this to decide when to call)
        side_effect    — True if needs HITL approval; False = safe to auto-run
        schema         — JSON Schema for args validation
        executor       — async function to actually run

Tools đang là MOCK trong Phase B2:
    - send_email     — Phase E sẽ wire MS Graph
    - create_task    — Phase E sẽ wire MS Graph (Planner/To-Do)
    - search_transcript — safe (no side-effect), auto-run

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


async def _exec_create_task(args: dict, *, session: AsyncSession, user_id: uuid.UUID) -> dict:
    """MOCK: simulate creating a task in MS Planner/To-Do."""
    title = args.get("title", "")
    assignee = args.get("assignee", "")
    deadline = args.get("deadline", "")
    logger.info(f"[MOCK create_task] title={title!r} assignee={assignee!r}")
    return {
        "status": "created_mock",
        "task_id": f"mock-task-{uuid.uuid4().hex[:8]}",
        "title": title,
        "assignee": assignee,
        "deadline": deadline,
        "note": "Mock execution — Phase E wires MS Planner.",
    }


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
            "Create a task assigned to someone with a deadline. "
            "Use when user asks to track an action item as a task. "
            "REQUIRES user approval before execution (side-effect)."
        ),
        "side_effect": True,
        "schema": {
            "type": "object",
            "required": ["title", "assignee"],
            "properties": {
                "title": {"type": "string"},
                "assignee": {"type": "string", "description": "Name or email"},
                "deadline": {"type": "string", "description": "ISO date or 'YYYY-MM-DD'"},
                "description": {"type": "string"},
            },
        },
        "executor": _exec_create_task,
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
