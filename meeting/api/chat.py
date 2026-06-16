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
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.auth import get_current_user
from meeting.auth.tokens import ReauthRequired, get_graph_access_token
from meeting.db import get_session
from meeting.db import repositories as repo
from meeting.db.base import AsyncSessionLocal
from meeting.db.models import PendingAction, User
from meeting.graphs import (
    get_checkpointer,
    resume_chat_turn,
    run_chat_turn,
    stream_chat_turn,
)
from meeting.graphs._chat_llm import _llm_client, _llm_model
# (role now comes from the authenticated user's users.role_id, not AgentBase)
from meeting.services.kickoff import run_kickoff
from meeting.services.redmine_mcp_client import get_redmine_mcp_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# Azure AD object-id (GUID) shape — what pm-agent's direct-oid path accepts.
_OID_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


# ─── Schemas ──────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    meeting_id: Optional[str] = None
    title: Optional[str] = None


class MessageSend(BaseModel):
    text: str
    # The UI-selected project for THIS turn (user-scoped sessions: grounding is
    # per-turn, not bound to the session). None → answer without project grounding.
    meeting_id: Optional[str] = None


class SessionRename(BaseModel):
    title: str


class KickoffRequest(BaseModel):
    # Optional dev override (VITE_KICKOFF_ROLE). Default path uses the logged-in
    # user's resolved role (users.role_id). Falls back to a generic greeting.
    role: Optional[str] = None


def _pick_role_name(
    request_role: Optional[str], user_role: Optional[str]
) -> Optional[str]:
    """The role for a kickoff: the dev override wins, else the user's resolved
    role, else None (→ generic greeting)."""
    return (request_role or "").strip() or user_role


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
    req: SessionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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
async def list_sessions(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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


@router.post("/sessions/{session_id}/kickoff")
async def kickoff_session(
    session_id: str,
    req: KickoffRequest = KickoffRequest(),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Mee speaks first: a role-tailored, data-grounded greeting on an empty
    thread. Idempotent — if the thread already has messages, do nothing. Never
    raises on a kickoff failure; `run_kickoff` degrades to a generic greeting.
    """
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    # Only kick off an empty thread (the FE also guards; this is the backstop).
    existing = await repo.list_chat_messages(session, sid, limit=1)
    if existing:
        return {"reply": None, "skipped": True}

    user_role_name = user.role.name if user.role else None
    role_name = _pick_role_name(req.role, user_role_name)
    role = await repo.get_role(session, role_name) if role_name else None
    user_name = (user.display_name or "").strip() or "bạn"

    async def _call_tool(name: str) -> dict:
        return await get_redmine_mcp_client().call_tool(name, {})

    def _generate(messages: list[dict]) -> str:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=_llm_model(), messages=messages, temperature=0.7, max_tokens=400,
        )
        return resp.choices[0].message.content or ""

    greeting = await run_kickoff(
        role=role, user_name=user_name, call_tool=_call_tool, generate=_generate,
    )

    await repo.add_chat_message(
        session,
        session_id=sid,
        role="agent",
        content={"text": greeting},
        metadata={"intent": "kickoff", "role": role_name},
    )
    return {"reply": greeting, "role": role_name}


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


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str, session: AsyncSession = Depends(get_session)
):
    """Remove a chat session permanently (user-scoped sidebar remove): hard-delete
    its messages + pending actions + the session row, and purge the LangGraph
    checkpoint thread. Distinct from clear (which keeps the row). 404 if missing;
    the checkpoint purge is best-effort and never 500s the delete."""
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    await repo.delete_chat_session(session, sid)

    try:
        await get_checkpointer().adelete_thread(str(sid))
    except Exception:
        logger.warning("delete: checkpoint purge failed for %s", sid, exc_info=True)

    return {"status": "deleted", "session_id": str(sid)}


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    req: SessionRename,
    session: AsyncSession = Depends(get_session),
):
    """Rename a chat session (set its title). 404 if the session is missing."""
    sid = _parse_uuid(session_id)
    chat = await repo.rename_chat_session(session, sid, req.title.strip())
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"id": str(chat.id), "title": chat.title}


# ─── Messages ─────────────────────────────────────────────────────

async def _graph_token_or_401(user: User, session: AsyncSession):
    """Resolve the bearer chat sends to pm-agent, per PM_AGENT_AUTH_MODE:

    - "jwt" (default): the user's Microsoft Graph access token → pm-agent's JWT
      path. None for mock users (no Graph token); 401 for real MS users whose
      stored refresh token is gone/expired (FE re-logins).
    - "oid" (temporary): the user's raw Azure OID → pm-agent's direct-oid test
      port. For deploys where real O365 login isn't active yet AND that port is
      still open. Mock users (no ms_oid) return None, so the client falls back
      to the static TOKEN_AUTHEN_PM_AGENT OID. No Graph call, never 401.
    """
    if os.environ.get("PM_AGENT_AUTH_MODE", "jwt").strip().lower() == "oid":
        # Only forward a real Azure OID (GUID). Legacy/dev rows like
        # ms_oid="dev-local-user" aren't GUIDs → pm-agent's direct-oid regex
        # rejects them and they fall to the static-key path → 401. Return None
        # for those so the client uses the static TOKEN_AUTHEN_PM_AGENT OID.
        oid = (user.ms_oid or "").strip()
        return oid if _OID_GUID_RE.match(oid) else None

    if not user.ms_oid:
        return None
    try:
        return await get_graph_access_token(user, session)
    except ReauthRequired:
        raise HTTPException(
            status_code=401,
            detail="Phiên Microsoft đã hết hạn — vui lòng đăng nhập lại.",
        )


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    req: MessageSend,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
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

    checkpointer = get_checkpointer()
    pm_token = await _graph_token_or_401(user, session)

    result = await run_chat_turn(
        session_id=session_id,
        user_id=str(user.id),
        user_message=req.text,
        meeting_id=req.meeting_id,
        session=session,
        checkpointer=checkpointer,
        pm_user_token=pm_token,
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
async def send_message_stream(
    session_id: str,
    req: MessageSend,
    user: User = Depends(get_current_user),
):
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
    # Capture identity as a plain value now: `user` is bound to the Depends
    # session, which is torn down before the StreamingResponse body runs.
    auth_user_id = user.id

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
                # Re-bind the authenticated user to this generator's own session
                # (the Depends one is closed) for token refresh + _persist_interrupt.
                user = await session.get(User, auth_user_id)
                checkpointer = get_checkpointer()
                try:
                    pm_token = await _graph_token_or_401(user, session)
                except HTTPException as e:
                    yield _sse({"type": "error", "detail": e.detail, "status": e.status_code})
                    return

                async for ev in stream_chat_turn(
                    session_id=session_id,
                    user_id=str(user.id),
                    user_message=req.text,
                    meeting_id=req.meeting_id,
                    session=session,
                    checkpointer=checkpointer,
                    pm_user_token=pm_token,
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
async def list_pending_actions(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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
        return await _persist_interrupt(session, action.session_id, user, result)

    return {
        "status": "rejected",
        "reply": result["reply"],
    }
