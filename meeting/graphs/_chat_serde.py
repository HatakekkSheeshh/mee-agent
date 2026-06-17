"""Pure serialization + payload helpers for the chat graph.

These touch NONE of the test-patched seams (repo / execute_tool / list_tools /
get_tool / build_task_items), so they move out cleanly. Re-imported into
chat_graph.py so every `chat_graph.X` reference (incl. tests calling
`chat_graph._reconcile_text` / `chat_graph._decision_to_payload`) still resolves.
"""
from __future__ import annotations

import json
import logging
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
