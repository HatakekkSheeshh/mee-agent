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

logger = logging.getLogger(__name__)


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
