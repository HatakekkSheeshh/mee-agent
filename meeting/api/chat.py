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

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import get_session
from meeting.db import repositories as repo
from meeting.db.models import PendingAction
from meeting.graphs import get_checkpointer, run_chat_turn, resume_chat_turn

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


class RejectionRequest(BaseModel):
    reason: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────

def _parse_uuid(s: str) -> uuid.UUID:
    try:
        return uuid.UUID(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {s}")


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
        # Persist pending_action so FE can fetch + user can approve later
        pa_data = result["pending_action"]
        action = await repo.create_pending_action(
            session,
            session_id=sid,
            user_id=user.id,
            thread_id=session_id,
            tool_name=pa_data["tool"],
            tool_args=pa_data["args"],
            rationale=pa_data.get("rationale"),
            checkpoint_id=result.get("checkpoint_id"),
        )
        return {
            "status": "interrupted",
            "pending_action_id": str(action.id),
            "pending_action": {
                "id": str(action.id),
                "tool": pa_data["tool"],
                "args": pa_data["args"],
                "rationale": pa_data.get("rationale"),
                "description": pa_data.get("description"),
            },
        }

    return {
        "status": "complete",
        "reply": result["reply"],
        "intent": result.get("intent"),
        "tool_result": result.get("tool_result"),
    }


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

    decision = {
        "action": "approved",
        "edited_args": req.edited_args,
        "reason": req.reason,
    }
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
        decision={"action": "rejected", "reason": req.reason},
        session=session,
        checkpointer=checkpointer,
    )

    return {
        "status": "rejected",
        "reply": result["reply"],
    }
