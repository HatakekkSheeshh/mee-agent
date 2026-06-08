"""
Chat Agent Graph — LangGraph với HITL pattern.

Flow:
    load_context → classify_intent → branch:
        ├─ "question" → answer (LLM RAG over MoM + transcript) → reply
        └─ "tool"     → propose_action → interrupt() ◄── pause for user approval
                            ↓ (resume)
                       execute_action → reply

Key concepts:
    - interrupt() pauses graph, persists state via checkpointer
    - Frontend gets `__interrupt__` event with pending_action data
    - User clicks Approve/Reject → API resumes graph với Command(resume={...})
    - Same thread_id → resume từ checkpoint just before interrupt()

Sources:
    - https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/
    - Vault [[HITL Pattern]] for design rationale
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.services import execute_tool, get_tool, list_tools
from meeting.services.pm_agent_client import (
    PmAgentError,
    PmAgentResult,
    get_pm_agent_client,
)

logger = logging.getLogger(__name__)

# Safety cap on the pm_call ⇄ pm_await loop against a misbehaving agent.
PM_MAX_ROUNDS = 6


# ─── State ────────────────────────────────────────────────────────

class ChatState(TypedDict, total=False):
    # Input
    session_id: str            # ChatSession.id (also LangGraph thread_id)
    user_id: str
    user_message: str          # latest user message text
    meeting_id: Optional[str]  # if chat is bound to a meeting

    # Loaded by load_context
    meeting_context: dict      # title, project_summary_json, recording_moms[]
    recent_messages: list[dict]  # last N messages from chat_messages

    # Filled by classify_intent
    intent: Literal["question", "tool", "pm_task"]
    proposed_tool: Optional[str]
    proposed_args: Optional[dict]
    rationale: Optional[str]

    # Filled after interrupt + resume
    user_decision: Optional[dict]  # {action: 'approved'|'rejected', edited_args?, reason?}

    # Filled by execute_action / answer
    tool_result: Optional[dict]
    final_reply: str           # text to show user

    # ── pm-agent A2A branch (intent == "pm_task") ──
    # All checkpointed (thread_id = session_id) so a multi-step pm-agent
    # conversation survives across approve/reject round-trips on one thread.
    pm_task_id: Optional[str]      # A2A task id; None on first call, set from result
    pm_context_id: Optional[str]   # A2A contextId; echoed with task_id on resume
    pm_next_payload: dict          # what pm_call sends next:
    #   {kind:"start"|"text", text} | {kind:"approval", approval_action, approval_input}
    pm_last: Optional[dict]        # last PmAgentResult, as a dict
    pm_pending: Optional[dict]     # payload handed to interrupt() for the FE
    pm_rounds: int                 # loop counter for PM_MAX_ROUNDS
    pm_route: Optional[str]        # pm_call → router hint: "await" | "reply" | "end"

    # Internal
    error: Optional[str]


# ─── LLM client ───────────────────────────────────────────────────

def _llm_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )


def _llm_model() -> str:
    return os.getenv("LLM_MODEL", "openai/gpt-oss-120b")


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
        return {"recent_messages": recent, "meeting_context": meeting_ctx}

    return load_context


async def classify_intent(state: ChatState) -> dict:
    """LLM: phân loại user_message → 'question' or 'tool', extract args."""
    msg = state["user_message"]
    meeting = state.get("meeting_context", {})
    tools_spec = list_tools()

    system_prompt = f"""Bạn là Mee — agent trợ lý cuộc họp. Phân loại tin nhắn của user và respond với JSON.

Bạn có các tools sau:
{json.dumps(tools_spec, ensure_ascii=False, indent=2)}

Meeting context hiện tại:
- Title: {meeting.get('title', 'no meeting')}
- Purpose: {meeting.get('purpose', '')}
- Project summary available: {meeting.get('project_summary_json') is not None}
- Recordings with MoM: {len(meeting.get('recording_moms', []))}

PHÂN LOẠI:
1. "question" — user hỏi về nội dung họp / MoM / tóm tắt → trả lời trực tiếp, KHÔNG cần tool
2. "tool" — user yêu cầu hành động cần tool nội bộ (gửi email, tạo task, search transcript)
3. "pm_task" — user yêu cầu thao tác quản lý dự án trên Redmine qua pm-agent:
   - Truy vấn/báo cáo issue (liệt kê, tìm, issue overdue / stale / sắp đến hạn, workload…)
   - Tạo / cập nhật / cập nhật hàng loạt issue (các thao tác ghi sẽ cần user duyệt)
   Ví dụ: "tạo issue cho việc deploy v1", "liệt kê issue overdue của tôi",
   "cập nhật trạng thái issue #123". Với pm_task KHÔNG cần proposed_tool/args
   (pm-agent tự chọn skill); chỉ cần đặt intent = "pm_task".

Trả về CHỈ JSON (không markdown, không giải thích):
{{
  "intent": "question" | "tool" | "pm_task",
  "proposed_tool": "<tool_name>" or null,
  "proposed_args": {{...}} or null,
  "rationale": "<vì sao chọn intent/tool này — 1 câu ngắn>"
}}
"""
    user_prompt = f"Tin nhắn user: {msg}"

    try:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=_llm_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
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
        logger.info(
            f"[Node classify_intent] intent={parsed.get('intent')!r} "
            f"tool={parsed.get('proposed_tool')!r}"
        )
        return {
            "intent": parsed.get("intent", "question"),
            "proposed_tool": parsed.get("proposed_tool"),
            "proposed_args": parsed.get("proposed_args"),
            "rationale": parsed.get("rationale", ""),
        }
    except Exception as e:
        logger.exception("classify_intent failed")
        return {"intent": "question", "error": f"classify failed: {e}"}


def route_after_classify(
    state: ChatState,
) -> Literal["answer", "propose_action", "pm_call"]:
    """Conditional edge: pick branch based on intent."""
    intent = state.get("intent", "question")
    tool_name = state.get("proposed_tool")

    if intent == "pm_task":
        return "pm_call"

    if intent == "tool" and tool_name:
        tool_spec = get_tool(tool_name)
        if tool_spec and tool_spec.get("side_effect"):
            return "propose_action"
        # Tool without side-effect → run directly via answer path (auto-exec)
        return "propose_action"  # we still go through propose, but auto-approve
    return "answer"


async def answer_node(state: ChatState) -> dict:
    """LLM: trả lời user dựa trên meeting context + recent messages."""
    msg = state["user_message"]
    meeting = state.get("meeting_context", {})
    recent = state.get("recent_messages", [])

    mom_summary = ""
    # Prefer project summary (cross-recording context). Fall back to listing
    # individual recording MoMs if no project summary yet.
    project_summary = meeting.get("project_summary_json")
    recording_moms = meeting.get("recording_moms") or []
    if project_summary:
        narrative = project_summary.get("narrative", "")
        timeline = project_summary.get("decisions_timeline", [])
        mom_summary = (
            f"Project narrative: {narrative}\n"
            f"Decisions count: {sum(len(e.get('decisions', [])) for e in timeline)}\n"
        )
    elif recording_moms:
        # Show titles + summaries of each recording's MoM, truncated
        bits = []
        for rm in recording_moms[:5]:
            m = rm.get("mom_json") or {}
            bits.append(
                f"- {rm.get('session_label', 'phiên')}: {m.get('summary', '')[:200]}"
            )
        mom_summary = "Per-recording MoMs:\n" + "\n".join(bits)

    system_prompt = f"""Bạn là Mee — trợ lý cuộc họp thông minh. Trả lời ngắn gọn, tự nhiên, bằng tiếng Việt.

Meeting context:
{mom_summary or 'Chưa có MoM cho meeting này.'}
"""
    history = [{"role": "system", "content": system_prompt}]
    # Include recent messages (last 6 to keep prompt small)
    for m in recent[-6:]:
        if m["role"] == "user":
            history.append({"role": "user", "content": m["content"].get("text", "")})
        elif m["role"] == "agent":
            history.append({"role": "assistant", "content": m["content"].get("text", "")})
    history.append({"role": "user", "content": msg})

    try:
        client = _llm_client()
        resp = client.chat.completions.create(
            model=_llm_model(),
            messages=history,
            max_tokens=512,
            timeout=60,
        )
        reply = resp.choices[0].message.content.strip()
        logger.info(f"[Node answer] reply_len={len(reply)}")
        return {"final_reply": reply}
    except Exception as e:
        logger.exception("answer_node failed")
        return {"final_reply": f"(Lỗi: {e})", "error": str(e)}


async def propose_action_node(state: ChatState) -> dict:
    """
    Pause graph với interrupt() — chờ user approve.
    Frontend nhận pending_action info, hiển thị card, user click Approve/Reject.
    """
    tool_name = state.get("proposed_tool", "")
    tool_args = state.get("proposed_args", {}) or {}
    rationale = state.get("rationale", "")

    tool_spec = get_tool(tool_name)
    if not tool_spec:
        return {"final_reply": f"Tool không tồn tại: {tool_name}", "error": "unknown_tool"}

    # Tools không side-effect → auto-approve
    if not tool_spec.get("side_effect"):
        logger.info(f"[Node propose_action] auto-approving safe tool {tool_name}")
        return {"user_decision": {"action": "approved", "auto": True}}

    # Side-effect tool → interrupt for HITL
    logger.info(f"[Node propose_action] INTERRUPT — waiting for user approval on {tool_name}")
    decision = interrupt({
        "tool": tool_name,
        "args": tool_args,
        "rationale": rationale,
        "description": tool_spec.get("description", ""),
    })
    # When resumed with Command(resume={...}), `decision` = that dict
    logger.info(f"[Node propose_action] RESUMED with decision={decision}")
    return {"user_decision": decision}


def make_execute_action(session: AsyncSession):
    async def execute_action(state: ChatState) -> dict:
        """Sau khi user decision → execute tool nếu approved, else reply rejected."""
        decision = state.get("user_decision") or {}
        action = decision.get("action", "rejected")
        tool_name = state.get("proposed_tool", "")
        tool_args = state.get("proposed_args") or {}
        user_id = uuid.UUID(state["user_id"])

        if action == "rejected":
            reason = decision.get("reason", "user rejected")
            logger.info(f"[Node execute_action] REJECTED: {reason}")
            return {
                "final_reply": f"OK, không thực hiện {tool_name}. ({reason})",
                "tool_result": {"status": "rejected", "reason": reason},
            }

        # Approved — use edited args if provided
        if decision.get("edited_args"):
            tool_args = decision["edited_args"]

        logger.info(f"[Node execute_action] APPROVED — executing {tool_name}")
        result = await execute_tool(
            tool_name, tool_args, session=session, user_id=user_id
        )

        # Format reply
        if result.get("error"):
            reply = f"Đã thực hiện {tool_name} nhưng lỗi: {result['error']}"
        else:
            reply = f"Đã thực hiện {tool_name}. Kết quả: {result.get('status', 'ok')}"
            if tool_name == "send_email":
                reply = f"📧 Đã gửi email tới {tool_args.get('to', '?')}."
            elif tool_name == "create_task":
                reply = f"✓ Đã tạo task: {tool_args.get('title', '?')}."
            elif tool_name == "search_transcript":
                matches = result.get("matches", [])
                if matches:
                    reply = "Tìm thấy:\n" + "\n".join(f"• {m}" for m in matches[:5])
                else:
                    reply = "Không tìm thấy đoạn nào khớp."

        return {"tool_result": result, "final_reply": reply}

    return execute_action


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
        if state.get("proposed_tool"):
            agent_content["tool_called"] = state["proposed_tool"]

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
            if kind == "approval":
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
            return {
                "pm_rounds": rounds,
                "pm_route": "end",
                "final_reply": f"Xin lỗi, không kết nối được pm-agent: {e}",
                "tool_result": {"error": str(e), "via": "pm_agent"},
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


def route_after_pm_call(state: ChatState) -> Literal["pm_await", "pm_reply", "save_reply"]:
    route = state.get("pm_route")
    if route == "await":
        return "pm_await"
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


# ─── Builder ──────────────────────────────────────────────────────

def build_chat_graph(session: AsyncSession, checkpointer, pm_client=None):
    g = StateGraph(ChatState)

    g.add_node("load_context", make_load_context(session))
    g.add_node("classify_intent", classify_intent)
    g.add_node("answer", answer_node)
    g.add_node("propose_action", propose_action_node)
    g.add_node("execute_action", make_execute_action(session))
    # pm-agent branch. pm_client is injected in tests; in production the
    # pm_call node lazily resolves get_pm_agent_client() on first use, so
    # non-PM chats never require PM_AGENT_* to be configured.
    g.add_node("pm_call", make_pm_call(pm_client))
    g.add_node("pm_await", pm_await)
    g.add_node("pm_reply", pm_reply)
    g.add_node("save_reply", make_save_reply(session))

    g.set_entry_point("load_context")
    g.add_edge("load_context", "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_after_classify,
        {
            "answer": "answer",
            "propose_action": "propose_action",
            "pm_call": "pm_call",
        },
    )
    g.add_edge("answer", "save_reply")
    g.add_edge("propose_action", "execute_action")
    g.add_edge("execute_action", "save_reply")
    # pm-agent loop: pm_call → (await ⇄ pm_call) → pm_reply → save_reply
    g.add_conditional_edges(
        "pm_call",
        route_after_pm_call,
        {"pm_await": "pm_await", "pm_reply": "pm_reply", "save_reply": "save_reply"},
    )
    g.add_edge("pm_await", "pm_call")
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
    initial_state: ChatState = {
        "session_id": session_id,
        "user_id": user_id,
        "user_message": user_message,
        "meeting_id": meeting_id,
    }

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
