"""Deterministic markdown rendering for Redmine issue-list read tools.

The chat agent used to feed raw MCP JSON back to the LLM, which hand-rendered
the markdown table itself — and mangled rows on large tables (the #28815
blank-cell bug, see the spec). These five tools share one row schema, so we
render the table in code and short-circuit the LLM render round (agent.py).

Pure module: no I/O, no network. format_issue_list() returns None on anything
it cannot confidently render, so the caller falls back to the existing LLM flow.
"""
from __future__ import annotations

from typing import Any, Optional

# The five read tools sharing the issue-row schema
# (issue_id, subject, status, tracker, assigned_to, last_updated, url).
FORMATTABLE_READ_TOOLS = frozenset({
    "get_stale_issues",
    "get_overdue_issues",
    "get_issues_due_soon",
    "get_unassigned_issues",
    "list_redmine_issue",
})

_PLACEHOLDER = "—"
_STRIP_DOMAINS = ("@vng.com.vn", "@vng.vn")

# Canonical status display order; unknown statuses sort after these, alphabetically.
_STATUS_ORDER = ["New", "In Progress", "Resolved", "Feedback", "Closed", "Rejected"]

_TABLE_HEADER = "| # | Subject | Assignee | Updated |\n|---|---|---|---|"


def is_formattable(name: str) -> bool:
    """True if `name` is a read tool we render deterministically."""
    return name in FORMATTABLE_READ_TOOLS


def _extract_rows(result: Any) -> Optional[list]:
    """Locate the list of issue rows in a tool result, or None to fall back.

    Accepts a bare list or a dict carrying the rows under issues/result/data/items.
    An {"error": ...} result or any unrecognized shape returns None.
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if "error" in result:
            return None
        for key in ("issues", "result", "data", "items"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return None


def _clean(value: Any) -> str:
    """Cell text: coerce to str, collapse newlines, escape pipes; null/empty → '—'."""
    if value is None:
        return _PLACEHOLDER
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return _PLACEHOLDER
    return text.replace("|", "\\|")


def _assignee(value: Any) -> str:
    """Assignee cell: strip the company email domain for compactness; null → '—'."""
    if value is None:
        return _PLACEHOLDER
    text = str(value).strip()
    if not text:
        return _PLACEHOLDER
    for domain in _STRIP_DOMAINS:
        if text.endswith(domain):
            text = text[: -len(domain)]
            break
    return _clean(text)


def _status_sort_key(status: str):
    try:
        return (0, _STATUS_ORDER.index(status))
    except ValueError:
        return (1, status.lower())


def _row_date(row: dict) -> str:
    """last_updated as a sortable key; missing/null sorts last (ISO dates sort lexically)."""
    value = row.get("last_updated") if isinstance(row, dict) else None
    return str(value) if value else "9999-99-99"


def _group_and_sort(rows: list) -> list[tuple[str, list]]:
    """Group rows by status (canonical order), each group sorted oldest-updated first.

    Returns [(status, [rows...]), ...]. Rows with no status group under '—'.
    """
    groups: dict[str, list] = {}
    for row in rows:
        status = row.get("status") if isinstance(row, dict) else None
        groups.setdefault(str(status) if status else _PLACEHOLDER, []).append(row)
    ordered = sorted(groups.keys(), key=_status_sort_key)
    return [(status, sorted(groups[status], key=_row_date)) for status in ordered]


def _title(tool_name: str, args: dict, count: int) -> str:
    """Vietnamese header line per tool, including project + day threshold where set."""
    args = args or {}
    project = (args.get("project_name") or "").strip()
    days = args.get("days")
    suffix = f" — {project}" if project else ""
    if tool_name == "get_stale_issues":
        head = (f"{count} issue chưa cập nhật (≥{days} ngày)"
                if days is not None else f"{count} issue chưa cập nhật")
    elif tool_name == "get_overdue_issues":
        head = f"{count} issue quá hạn"
    elif tool_name == "get_issues_due_soon":
        head = (f"{count} issue sắp đến hạn (trong {days} ngày)"
                if days is not None else f"{count} issue sắp đến hạn")
    elif tool_name == "get_unassigned_issues":
        head = f"{count} issue chưa có người phụ trách"
    else:  # list_redmine_issue
        head = f"{count} issue"
    return f"**{head}{suffix}**"


def _issue_cell(row: dict) -> str:
    issue_id = row.get("issue_id")
    url = row.get("url")
    if url:
        return f"[{issue_id}]({url})"
    return str(issue_id) if issue_id is not None else _PLACEHOLDER


def _render_table(rows: list) -> str:
    lines = [_TABLE_HEADER]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {_issue_cell(row)} | {_clean(row.get('subject'))} "
            f"| {_assignee(row.get('assigned_to'))} | {_clean(row.get('last_updated'))} |"
        )
    return "\n".join(lines)


def format_issue_list(tool_name: str, args: dict, result: Any) -> Optional[str]:
    """Render an issue-list tool result as grouped markdown, or None to fall back.

    None  → unparseable/error result; caller keeps the existing LLM render flow.
    str   → ready-to-show markdown (a friendly message when there are 0 rows).
    """
    rows = _extract_rows(result)
    if rows is None:
        return None
    title = _title(tool_name, args, len(rows))
    if not rows:
        return f"{title}\n\nKhông có issue nào."
    sections = [title]
    for status, group_rows in _group_and_sort(rows):
        sections.append(f"### {status} ({len(group_rows)})\n{_render_table(group_rows)}")
    return "\n\n".join(sections)
