"""
Chat API — sessions + messages + HITL approval (Phase B2).

Endpoints:
    POST   /api/chat/sessions                 → create chat session (optional meeting_id)
    GET    /api/chat/sessions                 → list user's sessions
    GET    /api/chat/sessions/{id}            → session detail + messages
    POST   /api/chat/sessions/{id}/messages   → send message, get reply or pending_action
    GET    /api/chat/pending-actions          → list pending actions for user
    POST   /api/chat/pending-actions/{id}/approve  → approve + resume graph
    POST   /api/chat/pending-actions/{id}/reject   → reject + resume graph
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import get_session
from meeting.db import repositories as repo
from meeting.db.base import AsyncSessionLocal
from meeting.db.models import PendingAction
from meeting.graphs import (
    get_checkpointer,
    resume_chat_turn,
    run_chat_turn,
    stream_chat_turn,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


# ─── Schemas ──────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    meeting_id: Optional[str] = None
    title: Optional[str] = None


class MessageSend(BaseModel):
    text: str


class ApprovalRequest(BaseModel):
    edited_args: Optional[dict] = None
    reason: Optional[str] = None
    # pm-agent (A2A) additions:
    #   approval_action — "approve" | "edit" | "reject" for a need_approval step
    #   text            — free-text answer to a need_more_info step
    approval_action: Optional[str] = None
    text: Optional[str] = None


class RejectionRequest(BaseModel):
    reason: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────

# Sentinel tool_name marking a PendingAction that represents a pm-agent (A2A)
# HITL step rather than a local tool. Drives resume-payload shaping below.
PM_TOOL_NAME = "pm_agent"


def _parse_uuid(s: str) -> uuid.UUID:
    try:
        return uuid.UUID(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {s}")


def _is_pm_interrupt(pa_data: dict) -> bool:
    """A pm-agent interrupt carries a `kind` and no local `tool` key."""
    return pa_data.get("kind") in ("need_approval", "need_more_info") or "tool" not in pa_data


def _persist_fields(pa_data: dict) -> dict:
    """Map a graph interrupt payload → PendingAction fields + the API response body.

    Local-tool interrupt: {"tool","args","rationale","description"}.
    pm-agent interrupt:    {"kind":"need_approval"|"need_more_info","issues"?,"prompt"}.
    """
    if _is_pm_interrupt(pa_data):
        return {
            "tool_name": PM_TOOL_NAME,
            "tool_args": pa_data,
            "rationale": pa_data.get("prompt"),
            "response": {
                "tool": PM_TOOL_NAME,
                "args": pa_data,
                "kind": pa_data.get("kind"),
                "issues": pa_data.get("issues"),
                "prompt": pa_data.get("prompt"),
                "task_id": pa_data.get("task_id"),
            },
        }
    return {
        "tool_name": pa_data["tool"],
        "tool_args": pa_data.get("args") or {},
        "rationale": pa_data.get("rationale"),
        "response": {
            "tool": pa_data["tool"],
            "args": pa_data.get("args") or {},
            "rationale": pa_data.get("rationale"),
            "description": pa_data.get("description"),
        },
    }


def _approve_decision(
    tool_name: str,
    *,
    approval_action: Optional[str],
    text: Optional[str],
    edited_args: Optional[dict],
    reason: Optional[str],
) -> dict:
    """Build the resume decision for an approve. pm-agent steps carry an
    approval verb or free text; local tools keep the existing shape."""
    if tool_name == PM_TOOL_NAME:
        decision: dict = {}
        if approval_action:
            decision["approval_action"] = approval_action
        if text is not None:
            decision["text"] = text
        if not approval_action and text is None:
            decision["approval_action"] = "approve"  # default: confirm
        return decision
    return {"action": "approved", "edited_args": edited_args, "reason": reason}


def _reject_decision(tool_name: str, *, reason: Optional[str]) -> dict:
    if tool_name == PM_TOOL_NAME:
        return {"approval_action": "reject", "approval_input": reason or ""}
    return {"action": "rejected", "reason": reason}


async def _persist_interrupt(
    session: AsyncSession, sid: uuid.UUID, user, result: dict
) -> dict:
    """Persist a (possibly fresh) pending action and shape the interrupted response."""
    fields = _persist_fields(result["pending_action"])
    action = await repo.create_pending_action(
        session,
        session_id=sid,
        user_id=user.id,
        thread_id=str(sid),
        tool_name=fields["tool_name"],
        tool_args=fields["tool_args"],
        rationale=fields["rationale"],
        checkpoint_id=result.get("checkpoint_id"),
    )
    return {
        "status": "interrupted",
        "pending_action_id": str(action.id),
        "pending_action": {"id": str(action.id), **fields["response"]},
    }


# ─── Sessions ─────────────────────────────────────────────────────

@router.post("/sessions")
async def create_session(
    req: SessionCreate, session: AsyncSession = Depends(get_session)
):
    user = await repo.get_or_create_dev_user(session)
    meeting_uuid = _parse_uuid(req.meeting_id) if req.meeting_id else None
    chat = await repo.create_chat_session(
        session, user_id=user.id, meeting_id=meeting_uuid, title=req.title
    )
    return {
        "id": str(chat.id),
        "meeting_id": str(chat.meeting_id) if chat.meeting_id else None,
        "title": chat.title,
        "created_at": chat.created_at.isoformat(),
    }


@router.get("/sessions")
async def list_sessions(session: AsyncSession = Depends(get_session)):
    user = await repo.get_or_create_dev_user(session)
    sessions = await repo.list_chat_sessions_for_user(session, user.id)
    return [
        {
            "id": str(s.id),
            "meeting_id": str(s.meeting_id) if s.meeting_id else None,
            "title": s.title,
            "created_at": s.created_at.isoformat(),
            "last_activity_at": s.last_activity_at.isoformat(),
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str, session: AsyncSession = Depends(get_session)
):
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await repo.list_chat_messages(session, sid, limit=100)
    return {
        "id": str(chat.id),
        "meeting_id": str(chat.meeting_id) if chat.meeting_id else None,
        "title": chat.title,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.post("/sessions/{session_id}/clear")
async def clear_session(
    session_id: str, session: AsyncSession = Depends(get_session)
):
    """Clear a chat session in place: delete its messages + pending actions and
    purge the LangGraph checkpoint thread, keeping the session row (and its
    meeting_id binding). Resets `recent_messages` so the agent re-grounds."""
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    await repo.clear_chat_session(session, sid)

    # Purge the checkpoint thread — best-effort. The grounding reset relies on the
    # chat_messages deletion, not the checkpoint, so a purge failure is non-fatal.
    try:
        await get_checkpointer().adelete_thread(str(sid))
    except Exception:
        logger.warning("clear: checkpoint purge failed for %s", sid, exc_info=True)

    return {"status": "cleared", "session_id": str(sid)}


# ─── Messages ─────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    req: MessageSend,
    session: AsyncSession = Depends(get_session),
):
    """
    Send a message → run chat graph.

    Returns either:
        {status: "complete", reply: "..."}            — normal answer
        {status: "interrupted", pending_action_id: ".", pending_action: {...}}
                                                       — HITL pause
    """
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    user = await repo.get_or_create_dev_user(session)
    checkpointer = get_checkpointer()

    result = await run_chat_turn(
        session_id=session_id,
        user_id=str(user.id),
        user_message=req.text,
        meeting_id=str(chat.meeting_id) if chat.meeting_id else None,
        session=session,
        checkpointer=checkpointer,
    )

    if result["status"] == "interrupted":
        # Persist pending_action so FE can fetch + user can approve later.
        # Handles both local-tool and pm-agent interrupts (see _persist_fields).
        return await _persist_interrupt(session, sid, user, result)

    return {
        "status": "complete",
        "reply": result["reply"],
        "intent": result.get("intent"),
        "tool_result": result.get("tool_result"),
    }


def _sse(obj: dict) -> str:
    """One SSE frame. ensure_ascii=False keeps Vietnamese readable in transit."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(session_id: str, req: MessageSend):
    """
    Streaming variant of POST /messages (SSE).

    Frames (each `data: <json>`):
        {type:"step", step:"context"|"classify"|"tool_call"|"tool_done"|"pm", ...}
        {type:"interrupted", pending_action_id, pending_action}   — terminal
        {type:"complete", reply, intent, tool_result}             — terminal
        {type:"error", detail}                                    — terminal

    The blocking /messages endpoint stays as-is (tests + fallback transport).
    """
    sid = _parse_uuid(session_id)

    async def gen():
        # Own session: a Depends(get_session) session is torn down BEFORE a
        # StreamingResponse body runs, so the graph would hit a closed session.
        # Mirror get_session's commit/rollback contract manually.
        async with AsyncSessionLocal() as session:
            try:
                chat = await repo.get_chat_session(session, sid)
                if not chat:
                    yield _sse({"type": "error", "detail": "Session not found"})
                    return
                user = await repo.get_or_create_dev_user(session)
                checkpointer = get_checkpointer()

                async for ev in stream_chat_turn(
                    session_id=session_id,
                    user_id=str(user.id),
                    user_message=req.text,
                    meeting_id=str(chat.meeting_id) if chat.meeting_id else None,
                    session=session,
                    checkpointer=checkpointer,
                ):
                    if ev.get("type") != "result":
                        yield _sse(ev)
                        continue
                    result = ev["result"]
                    if result["status"] == "interrupted":
                        payload = await _persist_interrupt(session, sid, user, result)
                        await session.commit()
                        yield _sse({"type": "interrupted", **payload})
                    else:
                        await session.commit()
                        yield _sse({
                            "type": "complete",
                            "reply": result["reply"],
                            "intent": result.get("intent"),
                            "tool_result": result.get("tool_result"),
                        })
            except Exception as e:
                logger.exception("stream turn failed for session %s", session_id)
                await session.rollback()
                yield _sse({"type": "error", "detail": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Pending actions (HITL) ───────────────────────────────────────

@router.get("/pending-actions")
async def list_pending_actions(session: AsyncSession = Depends(get_session)):
    user = await repo.get_or_create_dev_user(session)
    stmt = (
        select(PendingAction)
        .where(PendingAction.user_id == user.id, PendingAction.status == "pending")
        .order_by(PendingAction.created_at.desc())
    )
    actions = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(a.id),
            "session_id": str(a.session_id),
            "tool_name": a.tool_name,
            "tool_args": a.tool_args,
            "rationale": a.rationale,
            "created_at": a.created_at.isoformat(),
        }
        for a in actions
    ]


@router.post("/pending-actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    req: ApprovalRequest,
    session: AsyncSession = Depends(get_session),
):
    aid = _parse_uuid(action_id)
    action = await repo.get_pending_action(session, aid)
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if action.status != "pending":
        raise HTTPException(
            status_code=400, detail=f"Action already {action.status}"
        )

    decision = _approve_decision(
        action.tool_name,
        approval_action=req.approval_action,
        text=req.text,
        edited_args=req.edited_args,
        reason=req.reason,
    )
    await repo.resolve_pending_action(
        session, aid, decision="approved",
        edited_args=req.edited_args, reason=req.reason,
    )

    # Resume graph
    checkpointer = get_checkpointer()
    result = await resume_chat_turn(
        session_id=action.thread_id,
        decision=decision,
        session=session,
        checkpointer=checkpointer,
    )

    # A pm-agent step may interrupt again (need_more_info → need_approval):
    # persist a fresh pending action and surface it like a first-turn interrupt.
    if result["status"] == "interrupted":
        user = await repo.get_or_create_dev_user(session)
        return await _persist_interrupt(session, action.session_id, user, result)

    # Mark as executed
    if result.get("tool_result"):
        await repo.mark_action_executed(
            session, aid, result["tool_result"],
            success=not result["tool_result"].get("error"),
        )

    return {
        "status": "executed",
        "reply": result["reply"],
        "tool_result": result.get("tool_result"),
    }


@router.post("/pending-actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    req: RejectionRequest,
    session: AsyncSession = Depends(get_session),
):
    aid = _parse_uuid(action_id)
    action = await repo.get_pending_action(session, aid)
    if not action:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if action.status != "pending":
        raise HTTPException(status_code=400, detail=f"Action already {action.status}")

    await repo.resolve_pending_action(
        session, aid, decision="rejected", reason=req.reason,
    )

    # Resume graph with rejection
    checkpointer = get_checkpointer()
    result = await resume_chat_turn(
        session_id=action.thread_id,
        decision=_reject_decision(action.tool_name, reason=req.reason),
        session=session,
        checkpointer=checkpointer,
    )

    # Rejecting one pm step can still lead to a follow-up prompt — surface it.
    if result["status"] == "interrupted":
        user = await repo.get_or_create_dev_user(session)
        return await _persist_interrupt(session, action.session_id, user, result)

    return {
        "status": "rejected",
        "reply": result["reply"],
    }
