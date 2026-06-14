"""pm-agent A2A branch — nodes, routers, payload handling.

Correctness constraint: the non-idempotent A2A send lives in pm_call (NO
interrupt). pm_await / pm_error are the only nodes that interrupt and perform no
send, so each pm_call sends exactly once across resume/replay.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from langgraph.types import interrupt

from meeting.graphs._chat_serde import _decision_to_payload, _result_to_dict
from meeting.graphs._chat_state import ChatState, PM_MAX_ROUNDS
from meeting.services.pm_agent_client import PmAgentError, get_pm_agent_client

logger = logging.getLogger(__name__)

def make_pm_call(pm_client):
    async def pm_call(state: ChatState) -> dict:
        """One A2A send per invocation (idempotent). Never interrupts."""
        rounds = state.get("pm_rounds", 0) + 1
        if rounds > PM_MAX_ROUNDS:
            logger.warning("[Node pm_call] PM_MAX_ROUNDS exceeded — aborting")
            return {
                "pm_rounds": rounds,
                "pm_route": "end",
                "final_reply": (
                    "Xin lỗi, yêu cầu với pm-agent lặp quá nhiều vòng nên mình "
                    "tạm dừng. Bạn thử diễn đạt lại nhé."
                ),
                "tool_result": {"status": "aborted", "reason": "max_rounds", "via": "pm_agent"},
            }

        payload = state.get("pm_next_payload") or {
            "kind": "start",
            "text": state.get("user_message", ""),
        }
        task_id = state.get("pm_task_id")
        context_id = state.get("pm_context_id")
        kind = payload.get("kind")
        # Per-request identity: the signed-in user's real Azure OID, forwarded as
        # the A2A bearer so pm-agent's direct-oid path acts as this user (not a
        # static env OID). None for unauthenticated/legacy callers → client
        # falls back to its static api_key.
        bearer = state.get("pm_user_oid")

        try:
            client = pm_client or get_pm_agent_client()
            if kind == "reconcile":
                data_part = {
                    "kind": "reconcile_items",
                    "project": payload.get("project", ""),
                    "items": payload.get("items", []),
                }
                result = await client.send_message(
                    payload.get("text", ""),
                    task_id=task_id, context_id=context_id, data_part=data_part,
                    bearer=bearer,
                )
            elif kind == "approval":
                data_part = {
                    "approval_action": payload.get("approval_action", "approve"),
                    "approval_input": payload.get("approval_input", ""),
                }
                result = await client.send_message(
                    "", task_id=task_id, context_id=context_id, data_part=data_part,
                    bearer=bearer,
                )
            else:
                # kind in ("start", "text"). DEFERRED SEAM (spec §5): transcript
                # context for the chat's bound meeting/recording could be folded
                # into `text` here. Trigger/shape TBD — no behavior added in v1.
                result = await client.send_message(
                    payload.get("text", ""), task_id=task_id, context_id=context_id,
                    bearer=bearer,
                )
        except PmAgentError as e:
            logger.exception("[Node pm_call] pm-agent call failed")
            # Don't end the turn: route to pm_error, which interrupts for a
            # retry. pm_next_payload is left untouched (preserved in the
            # checkpoint), so a retry re-runs pm_call with the same request.
            return {
                "pm_rounds": rounds,
                "pm_route": "error",
                "pm_last_error": str(e),
            }

        route = "await" if result.state == "input_required" else "reply"
        logger.info(
            f"[Node pm_call] round={rounds} state={result.state} "
            f"task_id={result.task_id!r} route={route}"
        )
        return {
            "pm_rounds": rounds,
            "pm_task_id": result.task_id or task_id,
            "pm_context_id": result.context_id or context_id,
            "pm_last": _result_to_dict(result),
            "pm_route": route,
        }

    return pm_call

def route_after_pm_call(
    state: ChatState,
) -> Literal["pm_await", "pm_reply", "pm_error", "save_reply"]:
    route = state.get("pm_route")
    if route == "await":
        return "pm_await"
    if route == "error":
        return "pm_error"
    if route == "end":
        return "save_reply"
    return "pm_reply"

async def pm_await(state: ChatState) -> dict:
    """The ONLY interrupt in the pm branch. No A2A send here (replay-safe)."""
    last = state.get("pm_last") or {}
    # The pm-agent thread (task) id — surfaced on the card so the FE/user can
    # see which pm-agent thread this pause belongs to and follow it up.
    task_id = state.get("pm_task_id") or last.get("task_id")
    if last.get("need_approval"):
        pending = {
            "kind": "need_approval",
            "issues": last.get("issues") or [],
            "prompt": last.get("text", ""),
            "task_id": task_id,
        }
    else:
        pending = {
            "kind": "need_more_info",
            "prompt": last.get("text", ""),
            "task_id": task_id,
        }

    logger.info(f"[Node pm_await] INTERRUPT kind={pending['kind']}")
    decision = interrupt(pending)
    # On resume, `decision` is the value passed to Command(resume=...).
    logger.info(f"[Node pm_await] RESUMED decision={decision}")
    return {"pm_pending": pending, "pm_next_payload": _decision_to_payload(decision)}

async def pm_reply(state: ChatState) -> dict:
    """Surface the pm-agent reply — or, when a chunked reconcile left payloads
    in pm_queue, accumulate this group's reply and start a FRESH pm task for
    the next group (per-assignee chunking: one small message/send per group so
    the agentbase gateway never times out on a big reconcile)."""
    last = state.get("pm_last") or {}
    text = last.get("text") or "(pm-agent không trả về nội dung)"
    queue = list(state.get("pm_queue") or [])

    if queue:
        logger.info("[Node pm_reply] group done — %d payload(s) left in queue", len(queue))
        return {
            "pm_replies": list(state.get("pm_replies") or []) + [text],
            "pm_next_payload": queue[0],
            "pm_queue": queue[1:],
            # New group = new pm-agent task: never resume the finished thread.
            "pm_task_id": None,
            "pm_context_id": None,
            "pm_rounds": 0,
            "pm_route": "next",
        }

    replies = list(state.get("pm_replies") or []) + [text]
    return {
        "final_reply": "\n\n---\n\n".join(replies),
        "tool_result": {
            "status": last.get("state"),
            "task_id": last.get("task_id"),
            "via": "pm_agent",
        },
    }


def route_after_pm_reply(state: ChatState) -> Literal["pm_call", "save_reply"]:
    """Drain the chunked-reconcile queue before ending the turn."""
    return "pm_call" if state.get("pm_route") == "next" else "save_reply"

async def pm_error(state: ChatState) -> dict:
    """Interrupt after a transient pm-agent transport error, offering a retry.

    Performs NO A2A send (replay-safe). `pm_next_payload` is preserved from the
    failed pm_call, so resuming with a retry re-sends the identical request.
    Resume decision: approve / {action:"retry"} → re-send; anything else → give up.
    """
    err = state.get("pm_last_error") or "lỗi không xác định"
    pending = {
        "kind": "pm_error",
        "prompt": f"Mất kết nối với pm-agent: {err}\n\nThử gửi lại yêu cầu?",
        "task_id": state.get("pm_task_id"),
    }
    logger.info("[Node pm_error] INTERRUPT (retry?) err=%r", err)
    decision = interrupt(pending)
    logger.info("[Node pm_error] RESUMED decision=%s", decision)
    d = decision or {}
    retry = d.get("action") == "retry" or d.get("approval_action") == "approve"
    if retry:
        return {"pm_pending": pending, "pm_route": "retry"}
    return {
        "pm_pending": pending,
        "pm_route": "end",
        "final_reply": "Đã hủy đồng bộ với pm-agent.",
        "tool_result": {"status": "cancelled", "via": "pm_agent"},
    }

def route_after_pm_error(state: ChatState) -> Literal["pm_call", "save_reply"]:
    """Retry → re-run pm_call (re-sends the preserved payload); else end."""
    return "pm_call" if state.get("pm_route") == "retry" else "save_reply"
