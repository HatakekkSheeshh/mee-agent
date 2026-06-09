"""
Chat Agent Graph — LangGraph với HITL pattern.

Flow:
    load_context → classify_intent (binary) → branch:
        ├─ "agent"   → unified native tool-calling agent:
        │                agent ⇄ agent_tools                       (read tools auto-run)
        │                  └ side-effect → agent_approve interrupt() → agent_execute ↺
        └─ "pm_task" → pm_call ⇄ pm_await interrupt() → pm_reply    (Redmine via pm-agent)
    → save_reply

Key concepts:
    - interrupt() pauses graph, persists state via checkpointer
    - Frontend gets `__interrupt__` event with pending_action data
    - User clicks Approve/Reject → API resumes graph với Command(resume={...})
    - Same thread_id → resume từ checkpoint just before interrupt()
    - Replay-safety: LLM/tool-exec nodes never interrupt; only *_approve / pm_await
      do, and they perform no side effects, so each tool/send runs exactly once.

Sources:
    - https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/
    - Vault [[HITL Pattern]] for design rationale
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Literal, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.graphs._chat_llm import _llm_client, _llm_model
from meeting.graphs._chat_prompts import (
    CLASSIFY_SYSTEM_PROMPT,
    _agent_system_prompt,
    _to_llm_messages,
)
from meeting.graphs._chat_state import ChatState, MAX_AGENT_ROUNDS, PM_MAX_ROUNDS
from meeting.services import build_task_items, execute_tool, get_tool, list_tools
from meeting.services.pm_agent_client import (
    PmAgentError,
    PmAgentResult,
    get_pm_agent_client,
)

logger = logging.getLogger(__name__)


# ─── Meeting resolution ───────────────────────────────────────────

async def resolve_meeting(
    session: AsyncSession,
    *,
    user_id,
    bound_meeting_id: Optional[str],
    title: Optional[str],
) -> dict:
    """Resolve which meeting the user means.

    Default = the chat's bound meeting_id. If a `title` is named, ILIKE-resolve
    the user's meetings (most-recent first) and pick the most recent match; on
    no match, fall back to the bound meeting.

    Returns {meeting_id, resolved_by: "bound"|"title", candidates: [{id,title}]}.
    """
    if title and title.strip():
        matches = await repo.find_meetings_by_title(session, user_id, title)
        if matches:
            return {
                "meeting_id": str(matches[0].id),
                "resolved_by": "title",
                "candidates": [{"id": str(m.id), "title": m.title} for m in matches],
            }
    return {"meeting_id": bound_meeting_id, "resolved_by": "bound", "candidates": []}


# ─── Nodes ────────────────────────────────────────────────────────

def make_load_context(session: AsyncSession):
    async def load_context(state: ChatState) -> dict:
        """Load meeting context + recent messages for the LLM prompt."""
        sid = uuid.UUID(state["session_id"])
        # Recent messages (last 10)
        messages = await repo.list_chat_messages(session, sid, limit=10)
        recent = [{"role": m.role, "content": m.content} for m in messages]

        meeting_ctx = {}
        chat_sess = await repo.get_chat_session(session, sid)
        if chat_sess and chat_sess.meeting_id:
            meeting = await repo.get_meeting(session, chat_sess.meeting_id)
            if meeting:
                meeting_ctx = {
                    "id": str(meeting.id),
                    "title": meeting.title,
                    # `purpose` moved to recording in migration 0012 — chat
                    # context could aggregate per-recording purposes if needed.
                    "project_summary_json": meeting.project_summary_json,
                    "recording_moms": [
                        {"recording_id": str(r.id),
                         "session_label": r.title or r.session_label,
                         "purpose": r.purpose,
                         "mom_json": r.mom_json}
                        for r in (meeting.recordings or [])
                        if r.mom_json
                    ],
                }

        logger.info(
            f"[Node load_context] session={state['session_id'][:8]}, "
            f"recent_msgs={len(recent)}, meeting={meeting_ctx.get('title', 'none')!r}"
        )
        return {
            "recent_messages": recent,
            "meeting_context": meeting_ctx,
            # Default scope for the agent's tools = the chat's bound meeting.
            # switch_meeting can re-scope this mid-conversation by title.
            "resolved_meeting_id": meeting_ctx.get("id") or state.get("meeting_id"),
        }

    return load_context


async def classify_intent(state: ChatState) -> dict:
    """Binary router: 'pm_task' (Redmine via pm-agent) vs 'agent' (everything else).

    The unified tool-calling agent handles all meeting Q&A + local tools, so the
    only split left is whether to hand off to the separate pm-agent A2A branch.
    """
    msg = state["user_message"]
    try:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=_llm_model(),
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                {"role": "user", "content": f"Tin nhắn user: {msg}"},
            ],
            max_tokens=64,
            timeout=60,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip code fences if any
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        intent = parsed.get("intent")
        if intent not in ("pm_task", "agent"):
            intent = "agent"
        logger.info(f"[Node classify_intent] intent={intent!r}")
        return {"intent": intent}
    except Exception as e:
        logger.exception("classify_intent failed")
        return {"intent": "agent", "error": f"classify failed: {e}"}


def route_entry(state: ChatState) -> Literal["pm_call", "agent"]:
    """Conditional edge after classify: pm-agent branch, or the unified agent."""
    return "pm_call" if state.get("intent") == "pm_task" else "agent"


def make_save_reply(session: AsyncSession):
    async def save_reply(state: ChatState) -> dict:
        """Persist user msg + agent reply into chat_messages."""
        sid = uuid.UUID(state["session_id"])

        # Save user message
        await repo.add_chat_message(
            session,
            session_id=sid,
            role="user",
            content={"text": state["user_message"]},
        )

        # Save agent reply
        agent_content = {"text": state.get("final_reply", "")}
        if state.get("tool_result"):
            agent_content["tool_result"] = state["tool_result"]
        tools_called = [
            tc["function"]["name"]
            for m in (state.get("agent_messages") or [])
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
        ]
        if tools_called:
            agent_content["tools_called"] = tools_called

        await repo.add_chat_message(
            session,
            session_id=sid,
            role="agent",
            content=agent_content,
            metadata={"intent": state.get("intent")},
        )
        logger.info(f"[Node save_reply] persisted 2 messages")
        return {}

    return save_reply


# ─── Unified tool-calling agent (intent == "agent") ───────────────
#
# Path A: native OpenAI tool-calling (verified via scripts/probe_tool_calling.py
# against the MaaS gemma endpoint — reliable tool_calls + parseable args).
#
# Replay-safety mirrors the pm branch: the LLM call (agent) and tool execution
# (agent_tools, agent_execute) NEVER interrupt; agent_approve is the ONLY node
# that interrupts and it performs no side effects. So a side-effect tool runs
# exactly once (in agent_execute, after resume), never re-run on replay.
#
#   agent ─ tools? ─► agent_tools ─ side-effect? ─► agent_approve ─► agent_execute ─┐
#     ▲                   │ read-only                                               │
#     └───────────────────┴──────────────────────◄─────────────────────────────────┘
#   agent ─ no tool ─► (finish) ─► save_reply


def _openai_tools() -> list[dict]:
    """Tool registry → OpenAI tool schemas, with meeting_id stripped (the agent
    never supplies it; we inject resolved_meeting_id server-side)."""
    out = []
    for s in list_tools():
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


def _tc_to_dict(tc) -> dict:
    """Serialize an OpenAI tool_call object into a checkpointable dict."""
    return {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }


def _parse_tool_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[agent] could not parse tool arguments: %r", raw)
        return {}


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _inject_meeting(args: dict, name: str, resolved: Optional[str]) -> dict:
    """Inject the resolved meeting_id into a tool's args when the tool takes one
    and the model didn't supply it."""
    args = dict(args or {})
    if resolved and "meeting_id" not in args:
        spec = get_tool(name) or {}
        props = (spec.get("schema") or {}).get("properties") or {}
        if "meeting_id" in props:
            args["meeting_id"] = resolved
    return args


def _reconcile_text(project: str, items: list[dict]) -> str:
    """Phrase a reconcile request pm-agent's reconcile_check_info can parse:
    a target project + a numbered list of items."""
    header = f"Đối chiếu và tạo/cập nhật các công việc sau trên dự án {project or '(chưa rõ)'}:"
    lines = [header]
    for i, it in enumerate(items, 1):
        parts = [it.get("subject", "")]
        if it.get("assignee"):
            parts.append(f"phụ trách {it['assignee']}")
        if it.get("due_date"):
            parts.append(f"hạn {it['due_date']}")
        lines.append(f"{i}. " + " — ".join(p for p in parts if p))
    return "\n".join(lines)


async def _build_reconcile_template(
    session: AsyncSession,
    args: dict,
    meeting_ctx: dict,
    resolved_meeting_id: Optional[str],
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
        items = build_task_items(action_items)
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


def _last_assistant_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    return ""


def _seed_agent_messages(state: ChatState) -> list[dict]:
    """Build the initial OpenAI message list from recent history + this turn."""
    msgs: list[dict] = []
    for m in (state.get("recent_messages") or [])[-6:]:
        content = (m.get("content") or {}).get("text", "")
        if not content:
            continue
        if m.get("role") == "user":
            msgs.append({"role": "user", "content": content})
        elif m.get("role") == "agent":
            msgs.append({"role": "assistant", "content": content})
    msgs.append({"role": "user", "content": state.get("user_message", "")})
    return msgs


def make_agent(llm=None):
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

        client = llm or _llm_client()
        try:
            resp = client.chat.completions.create(
                model=_llm_model(),
                messages=_to_llm_messages(state, messages),
                tools=_openai_tools(),
                tool_choice="auto",
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


def make_agent_tools(session: AsyncSession):
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
            args = _inject_meeting(_parse_tool_args(tc["function"]["arguments"]), name, resolved)
            spec = get_tool(name)
            if spec and spec.get("side_effect"):
                if pending is None:
                    if name == "create_task":
                        template = await _build_reconcile_template(
                            session, args, state.get("meeting_context") or {}, resolved
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
                result = await execute_tool(name, args, session=session, user_id=user_id)
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


async def agent_approve(state: ChatState) -> dict:
    """The ONLY interrupt in the agent branch. No side effects (replay-safe).

    Surfaces the pending side-effect tool as a local-tool pending action
    ({tool, args, rationale, description}) — the existing api/chat.py machinery
    persists it and approve/reject resume with {action: approved|rejected, ...}.
    """
    pending = state.get("pending_tool") or {}
    spec = get_tool(pending.get("name", "")) or {}
    decision = interrupt({
        "tool": pending.get("name"),
        "args": pending.get("args") or {},
        "rationale": _last_assistant_text(state.get("agent_messages") or []),
        "description": spec.get("description", ""),
    })
    logger.info(f"[Node agent_approve] RESUMED decision={decision}")
    return {"user_decision": decision}


def make_agent_execute(session: AsyncSession):
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

        if action == "approved":
            if decision.get("edited_args"):
                args = _inject_meeting(
                    decision["edited_args"], name, state.get("resolved_meeting_id")
                )
            result = await execute_tool(name, args, session=session, user_id=user_id)
        else:
            result = {"status": "rejected", "reason": decision.get("reason", "user rejected")}

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


def route_after_agent_execute(state: ChatState) -> Literal["agent", "pm_call"]:
    """After an approved create_task, bridge into the pm reconcile loop;
    otherwise loop back to the agent (normal side-effect tools)."""
    return "pm_call" if state.get("agent_route") == "reconcile" else "agent"


# ─── pm-agent A2A branch ──────────────────────────────────────────
#
# Correctness constraint (LangGraph replays an interrupted node from its top
# on resume): the non-idempotent A2A send MUST live in pm_call, which has NO
# interrupt(). pm_await is the ONLY node that interrupts — it performs no send.
# So each pm_call invocation sends exactly once, and resuming re-runs only
# pm_await (recomputing its idempotent pending payload), never re-sending.


def _result_to_dict(result: PmAgentResult) -> dict:
    return {
        "task_id": result.task_id,
        "state": result.state,
        "text": result.text,
        "need_approval": result.need_approval,
        "issues": result.issues,
        "context_id": result.context_id,
    }


def _decision_to_payload(decision: Optional[dict]) -> dict:
    """Map a resume decision (from the API/FE) → the next pm_call payload."""
    decision = decision or {}

    # Explicit pm-agent approval verb wins.
    action = decision.get("approval_action")
    if action in ("approve", "edit", "reject"):
        return {
            "kind": "approval",
            "approval_action": action,
            "approval_input": decision.get("approval_input") or decision.get("text") or "",
        }

    # Generic local-tool style decision (approved/rejected) → approval verb.
    act = decision.get("action")
    if act == "approved":
        return {
            "kind": "approval",
            "approval_action": "approve",
            "approval_input": decision.get("approval_input") or decision.get("text") or "",
        }
    if act == "rejected":
        return {
            "kind": "approval",
            "approval_action": "reject",
            "approval_input": decision.get("reason") or "",
        }

    # Otherwise: free-text answer to a need_more_info prompt.
    text = decision.get("text") or decision.get("approval_input") or ""
    return {"kind": "text", "text": text}


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
                )
            elif kind == "approval":
                data_part = {
                    "approval_action": payload.get("approval_action", "approve"),
                    "approval_input": payload.get("approval_input", ""),
                }
                result = await client.send_message(
                    "", task_id=task_id, context_id=context_id, data_part=data_part
                )
            else:
                # kind in ("start", "text"). DEFERRED SEAM (spec §5): transcript
                # context for the chat's bound meeting/recording could be folded
                # into `text` here. Trigger/shape TBD — no behavior added in v1.
                result = await client.send_message(
                    payload.get("text", ""), task_id=task_id, context_id=context_id
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
    last = state.get("pm_last") or {}
    return {
        "final_reply": last.get("text") or "(pm-agent không trả về nội dung)",
        "tool_result": {
            "status": last.get("state"),
            "task_id": last.get("task_id"),
            "via": "pm_agent",
        },
    }


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


# ─── Builder ──────────────────────────────────────────────────────

def build_chat_graph(session: AsyncSession, checkpointer, pm_client=None, agent_llm=None):
    g = StateGraph(ChatState)

    g.add_node("load_context", make_load_context(session))
    g.add_node("classify_intent", classify_intent)
    # Unified tool-calling agent (question + local tools). agent_llm is injected
    # in tests; in production the agent node lazily resolves _llm_client().
    g.add_node("agent", make_agent(agent_llm))
    g.add_node("agent_tools", make_agent_tools(session))
    g.add_node("agent_approve", agent_approve)
    g.add_node("agent_execute", make_agent_execute(session))
    # pm-agent branch. pm_client is injected in tests; in production the
    # pm_call node lazily resolves get_pm_agent_client() on first use, so
    # non-PM chats never require PM_AGENT_* to be configured.
    g.add_node("pm_call", make_pm_call(pm_client))
    g.add_node("pm_await", pm_await)
    g.add_node("pm_reply", pm_reply)
    g.add_node("pm_error", pm_error)
    g.add_node("save_reply", make_save_reply(session))

    g.set_entry_point("load_context")
    g.add_edge("load_context", "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_entry,
        {"agent": "agent", "pm_call": "pm_call"},
    )
    # unified agent loop: agent ⇄ agent_tools → (agent_approve → agent_execute) ↺
    g.add_conditional_edges(
        "agent",
        route_after_agent,
        {"agent_tools": "agent_tools", "save_reply": "save_reply"},
    )
    g.add_conditional_edges(
        "agent_tools",
        route_after_agent_tools,
        {"agent": "agent", "agent_approve": "agent_approve"},
    )
    g.add_edge("agent_approve", "agent_execute")
    g.add_conditional_edges(
        "agent_execute",
        route_after_agent_execute,
        {"agent": "agent", "pm_call": "pm_call"},
    )
    # pm-agent loop: pm_call → (await ⇄ pm_call) → pm_reply → save_reply
    g.add_conditional_edges(
        "pm_call",
        route_after_pm_call,
        {
            "pm_await": "pm_await",
            "pm_reply": "pm_reply",
            "pm_error": "pm_error",
            "save_reply": "save_reply",
        },
    )
    g.add_edge("pm_await", "pm_call")
    g.add_conditional_edges(
        "pm_error",
        route_after_pm_error,
        {"pm_call": "pm_call", "save_reply": "save_reply"},
    )
    g.add_edge("pm_reply", "save_reply")
    g.add_edge("save_reply", END)

    return g.compile(checkpointer=checkpointer)


# ─── Runner ───────────────────────────────────────────────────────

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
    session_id: str, user_id: str, user_message: str, meeting_id: Optional[str]
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
        # reset unified-agent loop state
        "agent_messages": [],
        "agent_rounds": 0,
        "agent_route": None,
        "pending_tool": None,
        # reset pm-agent branch state (new message = new pm request)
        "pm_rounds": 0,
        "pm_next_payload": None,
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
) -> dict:
    """
    Run 1 turn of chat. May interrupt if tool needs approval.

    Returns:
        {"status": "complete", "reply": "...", ...}
            — graph finished without interrupt
        {"status": "interrupted", "pending_action": {...}, ...}
            — waiting for user approval
    """
    graph = build_chat_graph(session, checkpointer)
    config = {"configurable": {"thread_id": session_id}}
    initial_state = _initial_turn_state(session_id, user_id, user_message, meeting_id)

    logger.info(f"=== Running ChatGraph turn for session {session_id[:8]} ===")
    result = await graph.ainvoke(initial_state, config=config)
    return await _interrupt_or_complete(graph, config, result, session_id)


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

    logger.info(f"=== Resuming ChatGraph session {session_id[:8]} with decision={decision}")
    result = await graph.ainvoke(Command(resume=decision), config=config)
    return await _interrupt_or_complete(graph, config, result, session_id)
