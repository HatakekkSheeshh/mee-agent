"""Unified tool-calling agent branch — nodes, routers, and tool helpers.

Path A: native OpenAI tool-calling. Replay-safety: the LLM call (agent) and tool
execution (agent_tools/agent_execute) NEVER interrupt; agent_approve is the ONLY
node that interrupts and performs no side effects, so a side-effect tool runs
exactly once. The `tools` bundle (default = meeting.services) is the DI seam.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Literal, Optional

from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

import meeting.services as _services  # default toolset bundle (DI seam)
from meeting.db import repositories as repo
from meeting.graphs._chat_llm import _llm_client, _llm_model
from meeting.graphs._chat_prompts import _to_llm_messages
from meeting.graphs._chat_serde import (
    _json,
    _last_assistant_text,
    _parse_tool_args,
    _reconcile_text,
    _seed_agent_messages,
    _tc_to_dict,
)
from meeting.graphs._chat_state import ChatState, MAX_AGENT_ROUNDS

logger = logging.getLogger(__name__)

# Canned acknowledgement for a rejected side-effect tool. The reject ends the turn
# deterministically (route="finish") instead of looping back to the LLM, which would
# re-read the standing user instruction from the checkpoint and re-attempt the action.
REJECT_REPLY = "Đã hủy. Tui hong tạo task nữa."

def _openai_tools(*, tools=_services) -> list[dict]:
    """Tool registry → OpenAI tool schemas, with meeting_id stripped (the agent
    never supplies it; we inject resolved_meeting_id server-side)."""
    out = []
    for s in tools.list_tools():
        schema = json.loads(json.dumps(s.get("schema") or {"type": "object", "properties": {}}))
        props = schema.get("properties") or {}
        props.pop("meeting_id", None)
        schema["properties"] = props
        if "required" in schema:
            req = [r for r in schema["required"] if r != "meeting_id"]
            if req:
                schema["required"] = req
            else:
                schema.pop("required", None)
        out.append({
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s.get("description", ""),
                "parameters": schema,
            },
        })
    return out

def _inject_meeting(args: dict, name: str, resolved: Optional[str], *, tools=_services) -> dict:
    """Inject the resolved meeting_id into a tool's args when the tool takes one
    and the model didn't supply it."""
    args = dict(args or {})
    if resolved and "meeting_id" not in args:
        spec = tools.get_tool(name) or {}
        props = (spec.get("schema") or {}).get("properties") or {}
        if "meeting_id" in props:
            args["meeting_id"] = resolved
    return args

async def _build_reconcile_template(
    session: AsyncSession,
    args: dict,
    meeting_ctx: dict,
    resolved_meeting_id: Optional[str],
    *,
    tools=_services,
) -> dict:
    """Build the reconcile template {project, items} for a create_task handoff.

    project defaults to the bound meeting's title (editable on the local card).
    items come from an explicit task in args, else the meeting's MoM action_items.
    """
    project = (meeting_ctx or {}).get("title") or ""
    explicit_title = args.get("title") or args.get("subject")
    if explicit_title:
        items = [{
            "subject": explicit_title,
            "assignee": args.get("assignee", ""),
            "due_date": args.get("deadline") or args.get("due_date", ""),
            "description": args.get("description", ""),
        }]
    elif resolved_meeting_id:
        action_items = await repo.get_mom_action_items(
            session, uuid.UUID(resolved_meeting_id)
        )
        items = tools.build_task_items(action_items)
        # "tạo task cho <người>" → keep the {project, items} shape but narrow to
        # that person's action items (matched on assignee/pic, case-insensitive).
        assignee = (args.get("assignee") or "").strip()
        if assignee:
            items = [
                it for it in items
                if assignee.lower() in (it.get("assignee") or "").lower()
            ]
    else:
        items = []
    return {"project": project, "items": items}

def make_agent(llm=None, *, tools=None):
    ts = tools or _services

    async def agent(state: ChatState) -> dict:
        """One LLM tool-calling turn. Never interrupts (replay-safe)."""
        rounds = state.get("agent_rounds", 0)
        messages = state.get("agent_messages") or _seed_agent_messages(state)

        if rounds >= MAX_AGENT_ROUNDS:
            logger.warning("[Node agent] MAX_AGENT_ROUNDS reached — forcing finish")
            return {
                "agent_messages": messages,
                "agent_route": "finish",
                "final_reply": _last_assistant_text(messages)
                or "Mình đã thử nhiều bước nhưng chưa hoàn tất được, bạn thử lại nhé.",
            }

        # Force grounding: on the FIRST turn of a content/recording question
        # (grounding="required"), use tool_choice="required" so gemma MUST emit a
        # tool call instead of regurgitating a stale summary from recent_messages.
        # Only round 0 is forced — round ≥1 stays "auto" so the post-tool answer
        # turn can finish and the loop terminates. Verified Task 0: the MaaS gemma
        # endpoint honors tool_choice="required".
        tool_choice = (
            "required"
            if state.get("grounding") == "required" and rounds == 0
            else "auto"
        )
        client = llm or _llm_client()
        try:
            resp = client.chat.completions.create(
                model=_llm_model(),
                messages=_to_llm_messages(state, messages),
                tools=_openai_tools(tools=ts),
                tool_choice=tool_choice,
                max_tokens=1024,
                timeout=60,
            )
        except Exception as e:
            logger.exception("[Node agent] LLM call failed")
            return {
                "agent_messages": messages,
                "agent_route": "finish",
                "final_reply": f"(Lỗi khi gọi mô hình: {e})",
                "error": str(e),
            }

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            reply = (msg.content or "").strip()
            logger.info(f"[Node agent] final answer (len={len(reply)})")
            return {
                "agent_messages": messages + [{"role": "assistant", "content": reply}],
                "agent_route": "finish",
                "final_reply": reply,
            }

        assistant_msg = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [_tc_to_dict(tc) for tc in tool_calls],
        }
        logger.info(
            "[Node agent] round=%d tool_calls=%s",
            rounds + 1, [tc.function.name for tc in tool_calls],
        )
        return {
            "agent_messages": messages + [assistant_msg],
            "agent_rounds": rounds + 1,
            "agent_route": "tools",
        }

    return agent

def make_agent_tools(session: AsyncSession, *, tools=None):
    ts = tools or _services

    async def agent_tools(state: ChatState) -> dict:
        """Run the assistant's tool_calls. Read tools execute now (idempotent);
        the first side-effect tool is deferred to agent_approve. No interrupt."""
        messages = list(state.get("agent_messages") or [])
        assistant = messages[-1] if messages else {}
        tool_calls = assistant.get("tool_calls") or []
        resolved = state.get("resolved_meeting_id")
        user_id = uuid.UUID(state["user_id"])

        pending = None
        switched = None
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = _inject_meeting(
                _parse_tool_args(tc["function"]["arguments"]), name, resolved, tools=ts
            )
            spec = ts.get_tool(name)
            if spec and spec.get("side_effect"):
                if pending is None:
                    if name == "create_task":
                        template = await _build_reconcile_template(
                            session, args, state.get("meeting_context") or {}, resolved,
                            tools=ts,
                        )
                        pending = {"id": tc["id"], "name": name, "args": template}
                    else:
                        pending = {"id": tc["id"], "name": name, "args": args}
                else:
                    # Only one action approved per round — keep the message list
                    # valid by giving extra side-effect calls a deferred result.
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": _json({"status": "deferred",
                                          "note": "một hành động được duyệt mỗi lần"}),
                    })
                continue

            if not spec:
                result = {"error": f"unknown tool: {name}"}
            else:
                result = await ts.execute_tool(name, args, session=session, user_id=user_id)
            if name == "switch_meeting" and isinstance(result, dict) and result.get("meeting_id"):
                switched = result["meeting_id"]
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _json(result)})

        out: dict = {"agent_messages": messages}
        if switched:
            out["resolved_meeting_id"] = switched
        if pending:
            out["pending_tool"] = pending
            out["agent_route"] = "approve"
        else:
            out["agent_route"] = "agent"
        return out

    return agent_tools

def make_agent_approve(*, tools=None):
    ts = tools or _services

    async def agent_approve(state: ChatState) -> dict:
        """The ONLY interrupt in the agent branch. No side effects (replay-safe).

        Surfaces the pending side-effect tool as a local-tool pending action
        ({tool, args, rationale, description}) — the existing api/chat.py machinery
        persists it and approve/reject resume with {action: approved|rejected, ...}.
        """
        pending = state.get("pending_tool") or {}
        spec = ts.get_tool(pending.get("name", "")) or {}
        decision = interrupt({
            "tool": pending.get("name"),
            "args": pending.get("args") or {},
            "rationale": _last_assistant_text(state.get("agent_messages") or []),
            "description": spec.get("description", ""),
        })
        logger.info(f"[Node agent_approve] RESUMED decision={decision}")
        return {"user_decision": decision}

    return agent_approve

def make_agent_execute(session: AsyncSession, *, tools=None):
    ts = tools or _services

    async def agent_execute(state: ChatState) -> dict:
        """Run the approved side-effect tool (or record rejection), append its
        result to the message list, then loop back to the agent."""
        pending = state.get("pending_tool") or {}
        decision = state.get("user_decision") or {}
        action = decision.get("action", "rejected")
        name = pending.get("name", "")
        tc_id = pending.get("id")
        args = pending.get("args") or {}
        user_id = uuid.UUID(state["user_id"])
        messages = list(state.get("agent_messages") or [])

        # Approved create_task → bridge into the pm reconcile loop (GATE 2 is
        # pm-agent's own write approval). The user may edit `project` on the card.
        if action == "approved" and name == "create_task":
            template = dict(args)  # {project, items}
            if decision.get("edited_args"):
                template.update(decision["edited_args"])
            project = template.get("project", "")
            items = template.get("items", [])
            logger.info(
                "[Node agent_execute] create_task → pm reconcile (%d item(s))", len(items)
            )
            return {
                "pending_tool": None,
                "user_decision": None,
                "agent_route": "reconcile",
                "pm_next_payload": {
                    "kind": "reconcile", "project": project, "items": items,
                    "text": _reconcile_text(project, items),
                },
                "pm_rounds": 0,
                "tool_result": {
                    "status": "reconcile_handoff", "project": project, "count": len(items),
                },
            }

        # Rejected side-effect tool → terminal. Append the rejected result (keeps the
        # message list valid), then finish the turn with a canned reply instead of
        # looping back to the agent — otherwise the LLM re-reads the standing user
        # instruction and re-attempts the whole tool sequence.
        if action != "approved":
            result = {"status": "rejected", "reason": decision.get("reason", "user rejected")}
            if tc_id is not None:
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": _json(result)})
            logger.info(f"[Node agent_execute] tool={name!r} action={action!r} → finish (terminal)")
            return {
                "agent_messages": messages,
                "pending_tool": None,
                "user_decision": None,
                "tool_result": result,
                "agent_route": "finish",
                "final_reply": REJECT_REPLY,
            }

        if decision.get("edited_args"):
            args = _inject_meeting(
                decision["edited_args"], name, state.get("resolved_meeting_id"), tools=ts
            )
        result = await ts.execute_tool(name, args, session=session, user_id=user_id)

        if tc_id is not None:
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": _json(result)})
        logger.info(f"[Node agent_execute] tool={name!r} action={action!r}")
        return {
            "agent_messages": messages,
            "pending_tool": None,
            "user_decision": None,
            "tool_result": result,
            "agent_route": "agent",
        }

    return agent_execute

def route_after_agent(state: ChatState) -> Literal["agent_tools", "save_reply"]:
    return "agent_tools" if state.get("agent_route") == "tools" else "save_reply"

def route_after_agent_tools(state: ChatState) -> Literal["agent", "agent_approve"]:
    return "agent_approve" if state.get("agent_route") == "approve" else "agent"

def route_after_agent_execute(state: ChatState) -> Literal["agent", "pm_call", "save_reply"]:
    """After an approved create_task, bridge into the pm reconcile loop; on a
    rejected side-effect tool, finish the turn (terminal); otherwise loop back to
    the agent (normal approved side-effect tools)."""
    route = state.get("agent_route")
    if route == "reconcile":
        return "pm_call"
    if route == "finish":
        return "save_reply"
    return "agent"
