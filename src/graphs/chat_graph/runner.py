"""Turn runner — run_chat_turn / resume_chat_turn + per-turn state reset."""
from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from src.graphs._chat_state import ChatState
from src.graphs.chat_graph.builder import build_chat_graph
from src.services import ensure_redmine_tools_registered

logger = logging.getLogger(__name__)

async def _interrupt_or_complete(graph, config, result: dict, session_id: str) -> dict:
    """Inspect the post-invoke snapshot: paused → interrupted, else complete.

    Shared by run_chat_turn and resume_chat_turn so that a *resume* which leads
    to another interrupt (e.g. need_more_info → need_approval in the pm-agent
    loop) is surfaced exactly like a first-turn interrupt.
    """
    snap = await graph.aget_state(config)
    if snap.next:  # has a next node = paused on interrupt()
        for task in snap.tasks:
            if task.interrupts:
                int_payload = task.interrupts[0].value
                logger.info("=== ChatGraph INTERRUPTED — pending action ===")
                return {
                    "status": "interrupted",
                    "pending_action": int_payload,
                    "thread_id": session_id,
                    "checkpoint_id": snap.config.get("configurable", {}).get("checkpoint_id"),
                }

    logger.info("=== ChatGraph turn complete ===")
    return {
        "status": "complete",
        "reply": result.get("final_reply", ""),
        "intent": result.get("intent"),
        "tool_result": result.get("tool_result"),
    }

def _initial_turn_state(
    session_id: str,
    user_id: str,
    user_message: str,
    meeting_id: Optional[str],
    pm_user_token: Optional[str] = None,
) -> "ChatState":
    """Initial state for a NEW user message.

    thread_id = session_id is reused for the whole chat session, so LangGraph's
    checkpoint carries the previous turn's loop state forward. We MUST clear the
    per-turn buffers/counters here, or:
      - agent_messages persists → the agent skips re-seeding → the new user
        message is dropped (agent re-answers stale context);
      - pm_rounds accumulates → a fresh pm message instantly hits PM_MAX_ROUNDS;
      - pm_task_id/pm_context_id/pm_next_payload reuse an ended pm-agent task.
    History is NOT lost: load_context reloads recent messages from the DB and
    the agent re-seeds from those + this message. resume_chat_turn does NOT use
    this (a resume must keep the mid-loop state).
    """
    return {
        "session_id": session_id,
        "user_id": user_id,
        "user_message": user_message,
        "meeting_id": meeting_id,
        "pm_user_token": pm_user_token,
        # reset unified-agent loop state
        "agent_messages": [],
        "agent_rounds": 0,
        "agent_route": None,
        "pending_tool": None,
        # reset pm-agent branch state (new message = new pm request)
        "pm_rounds": 0,
        "pm_next_payload": None,
        "pm_queue": [],
        "pm_replies": [],
        "pm_last": None,
        "pm_pending": None,
        "pm_route": None,
        "pm_last_error": None,
        "pm_task_id": None,
        "pm_context_id": None,
        # reset shared per-turn outputs
        "tool_result": None,
        "user_decision": None,
        "final_reply": "",
        "error": None,
    }

async def run_chat_turn(
    *,
    session_id: str,
    user_id: str,
    user_message: str,
    meeting_id: Optional[str],
    session: AsyncSession,
    checkpointer,
    pm_user_token: Optional[str] = None,
) -> dict:
    """
    Run 1 turn of chat. May interrupt if tool needs approval.

    `pm_user_token` is the signed-in user's Microsoft Graph access token,
    forwarded to pm-agent as the per-request bearer (see pm_call). Optional so
    tests/legacy callers that never touch the pm branch can omit it.

    Returns:
        {"status": "complete", "reply": "...", ...}
            — graph finished without interrupt
        {"status": "interrupted", "pending_action": {...}, ...}
            — waiting for user approval
    """
    graph = build_chat_graph(session, checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    initial_state = _initial_turn_state(session_id, user_id, user_message, meeting_id, pm_user_token)

    logger.info(f"=== Running ChatGraph turn for session {session_id[:8]} ===")
    # Lazily register Redmine MCP tools on the first authenticated turn, using
    # this user's per-user key (best-effort; never raises). Must run before the
    # agent enumerates the tool registry so newly-registered tools are offered.
    await ensure_redmine_tools_registered(user_id, session)
    result = await graph.ainvoke(initial_state, config=config)
    return await _interrupt_or_complete(graph, config, result, session_id)

def update_to_events(node: str, delta: dict) -> list[dict]:
    """Map one `astream(stream_mode="updates")` chunk to UI step events. Pure.

    Only nodes whose progress is meaningful to the user emit an event; the FE
    maps `step`/tool names to localized labels. Unknown nodes emit nothing, so
    new graph nodes degrade silently instead of breaking the stream.
    """
    if node == "load_context":
        return [{"type": "step", "step": "context"}]
    if node == "classify_intent":
        ev: dict = {"type": "step", "step": "classify"}
        if delta.get("intent"):
            ev["intent"] = delta["intent"]
        return [ev]
    if node == "agent":
        if delta.get("agent_route") != "tools":
            return []  # final answer surfaces via the terminal result event
        msgs = delta.get("agent_messages") or []
        last = msgs[-1] if msgs else {}
        names = [
            (tc.get("function") or {}).get("name", "?")
            for tc in (last.get("tool_calls") or [])
        ]
        return [{"type": "step", "step": "tool_call", "tools": names}]
    if node == "agent_tools":
        return [{"type": "step", "step": "tool_done"}]
    if node in ("pm_call", "pm_await"):
        return [{"type": "step", "step": "pm"}]
    return []

async def stream_chat_turn(
    *,
    session_id: str,
    user_id: str,
    user_message: str,
    meeting_id: Optional[str],
    session: AsyncSession,
    checkpointer,
    pm_user_token: Optional[str] = None,
) -> AsyncIterator[dict]:
    """Streaming variant of run_chat_turn.

    Yields `{type:"step", ...}` events while the graph runs, then a single
    terminal `{type:"result", result}` whose `result` has exactly the
    run_chat_turn shape (complete | interrupted) — so the API layer reuses the
    same persist/response code for both transports.
    """
    graph = build_chat_graph(session, checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    initial_state = _initial_turn_state(session_id, user_id, user_message, meeting_id, pm_user_token)

    logger.info(f"=== Streaming ChatGraph turn for session {session_id[:8]} ===")
    await ensure_redmine_tools_registered(user_id, session)  # lazy per-user tool discovery
    final_values: dict = {}
    async for chunk in graph.astream(initial_state, config=config, stream_mode="updates"):
        for node, delta in (chunk or {}).items():
            if node == "__interrupt__":
                continue  # surfaced via the post-stream snapshot below
            if isinstance(delta, dict):
                final_values.update(delta)
                for ev in update_to_events(node, delta):
                    yield ev

    yield {
        "type": "result",
        "result": await _interrupt_or_complete(graph, config, final_values, session_id),
    }

async def resume_chat_turn(
    *,
    session_id: str,
    decision: dict,  # {"action": "approved"|"rejected", ...} or pm {"approval_action"|"text"}
    session: AsyncSession,
    checkpointer,
) -> dict:
    """Resume graph after a user decision.

    May complete OR interrupt again (multi-step pm-agent HITL) — the return
    shape mirrors run_chat_turn so the API can persist a fresh pending action.
    """
    graph = build_chat_graph(session, checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    # A process restart between the originating turn and this resume would have
    # lost the in-memory tool registry (lazy discovery is per-process). Re-register
    # from the checkpoint's user so the pending tool can still execute.
    snap = await graph.aget_state(config)
    resume_user_id = (snap.values or {}).get("user_id")
    if resume_user_id:
        await ensure_redmine_tools_registered(resume_user_id, session)

    logger.info(f"=== Resuming ChatGraph session {session_id[:8]} with decision={decision}")
    result = await graph.ainvoke(Command(resume=decision), config=config)
    return await _interrupt_or_complete(graph, config, result, session_id)
