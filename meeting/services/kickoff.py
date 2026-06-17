"""Role-persona proactive kickoff.

When a user opens a chat, Mee speaks first with a greeting tailored to the user's
role and grounded in their live Redmine data. This module holds the pure pieces:

- ``role_data_plan`` — given a role's ``data_plan``, which Redmine MCP read tools
  to run (no user context, no I/O).
- ``build_kickoff_messages`` — assemble the kickoff LLM prompt from the role +
  fetched data.

See docs/superpowers/specs/2026-06-13-role-persona-kickoff-design.md.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def role_data_plan(data_plan: str | None) -> list[str]:
    """Return the Redmine MCP read tools to run for a role's ``data_plan``.

    Pure: knows nothing about the user. Unknown / ``minimal`` / ``None`` fetch
    nothing so the kickoff degrades to a data-free greeting (never guess numbers).
    """
    if data_plan == "own_tasks":
        return ["get_workload_by_assignee"]
    if data_plan == "cross_project":
        return ["list_redmine_issue", "get_unassigned_issues"]
    return []


# Builder meta-prompt — slots filled at runtime. Mirrors the spec's v1 draft
# (docs/superpowers/specs/2026-06-13-role-persona-kickoff-design.md, "Kickoff
# prompts"). The anti-fabrication guard ("không bịa") is always present.
_KICKOFF_META_PROMPT = """\
Bạn là Mee — trợ lý cuộc họp. Bạn đang CHỦ ĐỘNG mở đầu cuộc trò chuyện
(người dùng chưa nhắn gì). Người dùng: {user_name} — vai trò: {role_name}.
Mô tả vai trò: {role_description}
Định hướng mở đầu cho vai trò này: {role_kickoff_prompt}

Dữ liệu thực tế của người dùng hôm nay (nguồn DUY NHẤT, không bịa thêm):
{role_data}

Viết MỘT lời chào mở đầu bằng tiếng Việt:
- Xưng "Mee", chào hợp với vai trò.
- Bám SÁT dữ liệu trên (đúng số task, đúng tên project). Nếu không có dữ liệu,
  chào ngắn và mời người dùng bắt đầu — TUYỆT ĐỐI không bịa số liệu.
- Kết bằng một đề xuất/câu hỏi mời hành động (vd "bạn muốn xem/tạo task không?").
- 2–4 câu, tự nhiên, không markdown nặng, không liệt kê dài.\
"""

_NO_DATA_PLACEHOLDER = "(không có dữ liệu hôm nay)"


def build_kickoff_messages(
    *,
    user_name: str,
    role_name: str,
    role_description: str,
    role_kickoff_prompt: str,
    role_data: str,
) -> list[dict]:
    """Assemble the kickoff LLM messages from a role + the fetched live data.

    Pure prompt assembly — the single LLM call is the caller's side-effect.
    Returns one ``system`` message holding the filled meta-prompt.
    """
    content = _KICKOFF_META_PROMPT.format(
        user_name=user_name,
        role_name=role_name,
        role_description=role_description,
        role_kickoff_prompt=role_kickoff_prompt,
        role_data=role_data.strip() or _NO_DATA_PLACEHOLDER,
    )
    return [{"role": "system", "content": content}]


# ── Orchestration: fetch live data → generate greeting ──────────────────────

# Ultimate fallback greeting — used when the LLM call fails or returns empty, so
# chat-open is never blocked by a kickoff failure.
DEFAULT_KICKOFF = (
    "Chào bạn, mình là Mee — trợ lý cuộc họp. "
    "Bạn muốn xem công việc hôm nay hay tạo việc mới?"
)

# Per-role-None generic kickoff direction (no data fetch).
_GENERIC_KICKOFF_PROMPT = (
    "Chào ngắn gọn, giới thiệu Mee là trợ lý cuộc họp và mời người dùng hỏi "
    "hoặc giao việc."
)

# Human VI labels for the counts surfaced from each Redmine read tool.
_TOOL_LABELS = {
    "get_workload_by_assignee": "Số task đang giao cho bạn",
    "list_redmine_issue": "Số task trong các project",
    "get_unassigned_issues": "Số task chưa có người nhận",
}
# Keys a Redmine MCP result may use to hold the list of items.
_COUNT_KEYS = ("issues", "tasks", "data", "items", "results")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove Qwen3-style ``<think>`` reasoning (closed and unclosed)."""
    text = _THINK_RE.sub("", text or "")
    return _THINK_OPEN_RE.sub("", text).strip()


def _count_items(result: object) -> int | None:
    """Count the list of items in a Redmine MCP result, or None if not countable.

    Errored or unexpected-shape results return None so they're skipped (never
    fabricated into the greeting).
    """
    if not isinstance(result, dict) or "error" in result:
        return None
    for key in _COUNT_KEYS:
        val = result.get(key)
        if isinstance(val, list):
            return len(val)
    return None


def summarize_redmine_results(results: dict) -> str:
    """Turn {tool_name: result} into a compact VI count summary (the LLM's
    grounding data). Skips errored/uncountable tools. Empty string if nothing."""
    lines = []
    for name, result in (results or {}).items():
        n = _count_items(result)
        if n is None:
            continue
        lines.append(f"- {_TOOL_LABELS.get(name, name)}: {n}")
    return "\n".join(lines)


async def fetch_role_data(tools: list[str], *, call_tool) -> str:
    """Run each Redmine read tool and summarize. A failing tool is skipped
    (best-effort — the greeting degrades gracefully, never 500s)."""
    results: dict = {}
    for name in tools or []:
        try:
            results[name] = await call_tool(name)
        except Exception as e:  # one bad tool must not sink the kickoff
            logger.warning("kickoff: tool %s failed: %s", name, e)
    return summarize_redmine_results(results)


async def run_kickoff(*, role, user_name: str, call_tool, generate,
                      fallback: str = DEFAULT_KICKOFF) -> str:
    """Compose the kickoff greeting: role → data plan → fetch → LLM.

    `role` is a Role-like object or None (unknown/no persona → minimal greeting,
    no data fetch). `call_tool(name)` is async; `generate(messages)` returns the
    LLM text. Any LLM failure (or empty output) falls back to `fallback`.
    """
    if role is None:
        name, description = "", ""
        data_plan, kickoff_prompt = "minimal", _GENERIC_KICKOFF_PROMPT
    else:
        name = role.name
        description = role.description or ""
        data_plan = role.data_plan
        kickoff_prompt = role.kickoff_prompt or ""

    data = await fetch_role_data(role_data_plan(data_plan), call_tool=call_tool)
    messages = build_kickoff_messages(
        user_name=user_name,
        role_name=name,
        role_description=description,
        role_kickoff_prompt=kickoff_prompt,
        role_data=data,
    )
    try:
        greeting = _strip_think(generate(messages))
    except Exception as e:
        logger.warning("kickoff: LLM generate failed: %s", e)
        return fallback
    return greeting or fallback
