"""Pure serialization + payload helpers for the chat graph.

These touch NONE of the test-patched seams (repo / execute_tool / list_tools /
get_tool / build_task_items), so they move out cleanly. Re-imported into
chat_graph.py so every `chat_graph.X` reference (incl. tests calling
`chat_graph._reconcile_text` / `chat_graph._decision_to_payload`) still resolves.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from meeting.graphs._chat_state import ChatState
from meeting.services.pm_agent_client import PmAgentResult

logger = logging.getLogger(__name__)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


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


def _reconcile_text(project: str, items: list[dict], note: str = "") -> str:
    """Phrase a reconcile request pm-agent's reconcile_check_info can parse:
    a target project + a numbered list of items. `note` is the user's approval
    note from the create_task card — appended so pm-agent's LLM reconcile sees
    it (was previously persisted for audit only and consumed nowhere)."""
    header = f"Đối chiếu và tạo/cập nhật các công việc sau trên dự án {project or '(chưa rõ)'}:"
    lines = [header]
    for i, it in enumerate(items, 1):
        parts = [it.get("subject", "")]
        if it.get("assignee"):
            parts.append(f"phụ trách {it['assignee']}")
        if it.get("due_date"):
            parts.append(f"hạn {it['due_date']}")
        lines.append(f"{i}. " + " — ".join(p for p in parts if p))
    if note:
        lines.append(f"Ghi chú của người duyệt: {note}")
    return "\n".join(lines)


# A 23-item reconcile in one message/send timed out at the agentbase gateway
# mid-LLM-reconcile (HANDOFF: RemoteProtocolError — not auth, not our client
# timeout). Cap the items per send; groups larger than this are sub-chunked.
MAX_RECONCILE_ITEMS = 8


def _reconcile_payloads(project: str, items: list[dict], note: str = "") -> list[dict]:
    """Split one reconcile template into one pm_call payload per assignee group
    (sub-chunked at MAX_RECONCILE_ITEMS), so no single message/send carries a
    payload big enough to hit the gateway timeout. Always returns ≥1 payload
    (a single small/empty template stays one send — identical to the old shape).
    """
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault((it.get("assignee") or "").strip().lower(), []).append(it)

    chunks: list[list[dict]] = []
    for group in groups.values() or [[]]:
        for i in range(0, max(len(group), 1), MAX_RECONCILE_ITEMS):
            chunks.append(group[i:i + MAX_RECONCILE_ITEMS])

    return [
        {
            "kind": "reconcile",
            "project": project,
            "items": chunk,
            "text": _reconcile_text(project, chunk, note=note),
        }
        for chunk in chunks
    ]


def _last_assistant_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    return ""


def _completed_action_note(content: dict) -> str:
    """A marker appended to a past agent turn whose tools ran to success, so the
    flattened cross-turn seed carries an explicit 'already done' signal. Without
    it the model only sees prose and may RE-FIRE a completed side-effect tool
    (live 2026-06-15: turn-1 create_task re-fired by a turn-2 'liệt kê'). Empty
    string when the turn had no tools, or the tool result signalled an error
    (a failed action may legitimately be retried). Pairs with the matching
    'KHÔNG LẶP HÀNH ĐỘNG ĐÃ XONG' rule in _agent_system_prompt."""
    tools = [t for t in (content.get("tools_called") or []) if t]
    if not tools:
        return ""
    result = content.get("tool_result")
    if isinstance(result, dict) and result.get("error"):
        return ""
    return (
        "\n\n[Bối cảnh hệ thống: các công cụ sau đã CHẠY XONG ở lượt trước và "
        f"KHÔNG được gọi lại trừ khi lượt HIỆN TẠI yêu cầu rõ: {', '.join(tools)}.]"
    )


def _seed_agent_messages(state: ChatState) -> list[dict]:
    """Build the initial OpenAI message list from recent history + this turn."""
    msgs: list[dict] = []
    for m in (state.get("recent_messages") or [])[-6:]:
        content = m.get("content") or {}
        text = content.get("text", "")
        if not text:
            continue
        if m.get("role") == "user":
            msgs.append({"role": "user", "content": text})
        elif m.get("role") == "agent":
            msgs.append({"role": "assistant", "content": text + _completed_action_note(content)})
    msgs.append({"role": "user", "content": state.get("user_message", "")})
    return msgs


def _result_to_dict(result: PmAgentResult) -> dict:
    return {
        "task_id": result.task_id,
        "state": result.state,
        "text": result.text,
        "need_approval": result.need_approval,
        "issues": result.issues,
        "context_id": result.context_id,
    }


# ─── leaked tool-call recovery ──────────────────────────────────────
# minimax-m2.5 (and other Anthropic-XML-trained models) sometimes emit tool
# calls as TEXT in message.content instead of native OpenAI tool_calls, when
# the serving layer (VNG MaaS / vLLM) has no tool-call parser configured. The
# leaked shape:
#     minimax:tool_call                 (or wrapped in <minimax:tool_call>…)
#     <invoke name="send_email">
#       <parameter name="to">andvd6</parameter>
#       <parameter name="subject">…</parameter>
#     </invoke>                         (closing tags are sometimes omitted)
# We recover them client-side so the agent loop fires regardless of MaaS config.
_TOOLCALL_START_RE = re.compile(r"<minimax:tool_call>|minimax:tool_call|<invoke\b")
_INVOKE_BLOCK_RE = re.compile(
    r'<invoke\s+name="([^"]+)"\s*>(.*?)(?:</invoke>|(?=<invoke\b)|\Z)',
    re.DOTALL,
)
_PARAM_RE = re.compile(
    r'<parameter\s+name="([^"]+)"\s*>(.*?)(?:</parameter>|(?=<parameter\b)|\Z)',
    re.DOTALL,
)


def parse_leaked_tool_calls(content: Optional[str]) -> tuple[list[dict], str]:
    """Recover tool calls a model leaked into text content (minimax/Anthropic
    XML) into the native _tc_to_dict shape so the agent loop consumes them
    unchanged.

    Returns (tool_calls, cleaned_text):
      - tool_calls: [{id, type:"function", function:{name, arguments(JSON str)}}]
      - cleaned_text: prose before the tool-call region (often ""), stripped.
    Returns ([], content) when there is no tool-call markup (or it is present
    but unparseable — the original text is surfaced untouched).
    """
    if not content:
        return [], content or ""
    start = _TOOLCALL_START_RE.search(content)
    if not start:
        return [], content
    prose = content[: start.start()]
    region = content[start.start():]
    calls: list[dict] = []
    for i, m in enumerate(_INVOKE_BLOCK_RE.finditer(region)):
        args = {pm.group(1): pm.group(2).strip() for pm in _PARAM_RE.finditer(m.group(2))}
        calls.append({
            "id": f"call_{i}",
            "type": "function",
            "function": {"name": m.group(1), "arguments": json.dumps(args, ensure_ascii=False)},
        })
    if not calls:
        return [], content
    return calls, prose.strip()


# ─── create_task → Redmine MCP apply (P2) ──────────────────────────
# Map an approved create_task template item ({subject, assignee, due_date,
# description}) onto the deployed MCP write tools. LIVE-SCHEMA NOTE (probe
# 2026-06-12): create_redmine_issue REQUIRES tracker + assigned_to and exposes
# `due_date` as a REAL field; update_redmine_issue exposes `due_date` + `notes`.
# So due_date is passed DIRECTLY (never folded into description), and an item's
# free-text description becomes an update `notes` journal comment.
def redmine_create_args(project: str, item: dict) -> dict:
    """Map a create_task template item → create_redmine_issue args.

    Required fields are always present (tracker defaults to 'Task' when the item
    has none); optionals are included only when the item carries them.
    """
    args = {
        "project_name": project,
        "subject": item.get("subject", ""),
        "tracker": item.get("tracker") or "Task",
        "assigned_to": item.get("assignee", ""),
    }
    description = (item.get("description") or "").strip()
    if description:
        args["description"] = description
    due = (item.get("due_date") or "").strip()
    if due:
        args["due_date"] = due
    return args


def redmine_update_args(project: str, item: dict, issue_id: str) -> dict:
    """Map a template item → update_redmine_issue args (only present fields)."""
    args: dict = {"issue_id": str(issue_id), "project_name": project}
    if item.get("subject"):
        args["subject"] = item["subject"]
    if item.get("assignee"):
        args["assigned_to"] = item["assignee"]
    if item.get("due_date"):
        args["due_date"] = item["due_date"]
    if item.get("description"):
        args["notes"] = item["description"]
    return args


def summarize_redmine_apply(project: str, results: list[dict]) -> str:
    """Vietnamese summary of a batch apply ({subject, result} per item)."""
    failed = [r for r in results if (r.get("result") or {}).get("error")]
    ok_count = len(results) - len(failed)
    lines = [f"Đã đồng bộ {ok_count}/{len(results)} việc lên Redmine (dự án {project})."]
    for r in failed:
        lines.append(f"- ❌ {r.get('subject', '')}: {(r.get('result') or {}).get('error')}")
    return "\n".join(lines)


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
