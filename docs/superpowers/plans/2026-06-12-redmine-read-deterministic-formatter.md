# Deterministic Redmine Issue-List Formatter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Redmine issue-list read results as deterministic markdown in code and short-circuit the LLM render round, so large tables (the #28815 blank-cell bug) are exact, never truncated, with explicit `—` for null fields.

**Architecture:** A new pure module `meeting/graphs/chat_graph/redmine_format.py` renders the five issue-list tools (shared row schema) into grouped-by-status markdown. `agent_tools` (`agent.py`) short-circuits the loop when every tool call in the round is a formatter-registered read: it sets `final_reply` and a new `agent_route="done"` that routes straight to `save_reply`, skipping the second LLM round. Any non-formattable/mixed/side-effect round, or any result the formatter can't parse, falls through to the existing LLM flow unchanged.

**Tech Stack:** Python 3, LangGraph `StateGraph`, pytest. Backend venv at `venv/` — run tests with `venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-06-12-redmine-read-deterministic-formatter-design.md`

---

## File Structure

- **Create** `meeting/graphs/chat_graph/redmine_format.py` — pure rendering module. One responsibility: turn an issue-list tool result into markdown (or `None` to signal "fall back to LLM"). No I/O.
- **Modify** `meeting/graphs/chat_graph/agent.py` — `agent_tools` gains the short-circuit branch; `route_after_agent_tools` gains the `"save_reply"` return.
- **Modify** `meeting/graphs/chat_graph/builder.py` — add `"save_reply"` to the `agent_tools` conditional-edge map.
- **Create** `tests/meeting/test_redmine_format.py` — pure formatter tests + `agent_tools` short-circuit integration tests.

---

## Task 1: Module scaffold — `is_formattable` + `_extract_rows`

**Files:**
- Create: `meeting/graphs/chat_graph/redmine_format.py`
- Test: `tests/meeting/test_redmine_format.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_redmine_format.py
"""Deterministic Redmine issue-list rendering (pure) + agent_tools short-circuit."""
from __future__ import annotations

from meeting.graphs.chat_graph import redmine_format as rf


def test_is_formattable_covers_the_five_issue_list_tools():
    for name in (
        "get_stale_issues", "get_overdue_issues", "get_issues_due_soon",
        "get_unassigned_issues", "list_redmine_issue",
    ):
        assert rf.is_formattable(name) is True


def test_is_formattable_false_for_other_reads():
    for name in ("get_field_metadata", "get_redmine_issue_by_id", "create_redmine_issue", ""):
        assert rf.is_formattable(name) is False


def test_extract_rows_finds_issues_key():
    assert rf._extract_rows({"total": 2, "issues": [{"issue_id": 1}]}) == [{"issue_id": 1}]


def test_extract_rows_accepts_bare_list_and_alt_keys():
    assert rf._extract_rows([{"issue_id": 1}]) == [{"issue_id": 1}]
    assert rf._extract_rows({"result": [{"issue_id": 9}]}) == [{"issue_id": 9}]


def test_extract_rows_returns_none_on_error_or_unknown_shape():
    assert rf._extract_rows({"error": "boom"}) is None
    assert rf._extract_rows({"message": "no list here"}) is None
    assert rf._extract_rows("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'meeting.graphs.chat_graph.redmine_format'`

- [ ] **Step 3: Write minimal implementation**

```python
# meeting/graphs/chat_graph/redmine_format.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph/redmine_format.py tests/meeting/test_redmine_format.py
git commit -m "feat(redmine-format): module scaffold — is_formattable + row extraction"
```

---

## Task 2: Cell helpers — `_clean` and `_assignee`

**Files:**
- Modify: `meeting/graphs/chat_graph/redmine_format.py`
- Test: `tests/meeting/test_redmine_format.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/meeting/test_redmine_format.py
def test_clean_null_and_empty_become_placeholder():
    assert rf._clean(None) == "—"
    assert rf._clean("") == "—"
    assert rf._clean("   ") == "—"


def test_clean_escapes_pipes_and_strips_newlines():
    assert rf._clean("a | b") == "a \\| b"
    assert rf._clean("line1\nline2") == "line1 line2"
    assert rf._clean("  trimmed \r\n") == "trimmed"


def test_assignee_strips_company_domain():
    assert rf._assignee("hieunq3@vng.com.vn") == "hieunq3"
    assert rf._assignee("someone@vng.vn") == "someone"
    assert rf._assignee("annd2") == "annd2"          # already a username


def test_assignee_null_becomes_placeholder():
    assert rf._assignee(None) == "—"
    assert rf._assignee("") == "—"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute '_clean'`

- [ ] **Step 3: Write minimal implementation**

Add to `redmine_format.py` (after `_extract_rows`):

```python
_PLACEHOLDER = "—"
_STRIP_DOMAINS = ("@vng.com.vn", "@vng.vn")


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph/redmine_format.py tests/meeting/test_redmine_format.py
git commit -m "feat(redmine-format): cell helpers — null→— , pipe-escape, domain strip"
```

---

## Task 3: Group by status + sort oldest-first — `_group_and_sort`

**Files:**
- Modify: `meeting/graphs/chat_graph/redmine_format.py`
- Test: `tests/meeting/test_redmine_format.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/meeting/test_redmine_format.py
def test_group_and_sort_orders_groups_canonically_and_rows_oldest_first():
    rows = [
        {"issue_id": 1, "status": "In Progress", "last_updated": "2026-06-04"},
        {"issue_id": 2, "status": "New", "last_updated": "2026-06-01"},
        {"issue_id": 3, "status": "New", "last_updated": "2026-05-20"},
        {"issue_id": 4, "status": "Zeta-custom", "last_updated": "2026-06-02"},
    ]
    grouped = rf._group_and_sort(rows)
    # canonical order: New before In Progress; unknown status ('Zeta-custom') last
    assert [status for status, _ in grouped] == ["New", "In Progress", "Zeta-custom"]
    # within 'New', oldest last_updated first
    new_ids = [r["issue_id"] for r in grouped[0][1]]
    assert new_ids == [3, 2]


def test_group_and_sort_null_status_and_null_date():
    rows = [
        {"issue_id": 1, "status": None, "last_updated": None},
        {"issue_id": 2, "status": None, "last_updated": "2026-06-01"},
    ]
    grouped = rf._group_and_sort(rows)
    assert grouped[0][0] == "—"                       # null status grouped under placeholder
    # null last_updated sorts LAST within the group
    assert [r["issue_id"] for r in grouped[0][1]] == [2, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: FAIL — `AttributeError: ... '_group_and_sort'`

- [ ] **Step 3: Write minimal implementation**

Add to `redmine_format.py`:

```python
# Canonical status display order; unknown statuses sort after these, alphabetically.
_STATUS_ORDER = ["New", "In Progress", "Resolved", "Feedback", "Closed", "Rejected"]


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph/redmine_format.py tests/meeting/test_redmine_format.py
git commit -m "feat(redmine-format): group by status (canonical) + oldest-first sort"
```

---

## Task 4: Per-tool header line — `_title`

**Files:**
- Modify: `meeting/graphs/chat_graph/redmine_format.py`
- Test: `tests/meeting/test_redmine_format.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/meeting/test_redmine_format.py
def test_title_stale_includes_count_threshold_and_project():
    t = rf._title("get_stale_issues", {"project_name": "AI Innovation Projects", "days": 7}, 31)
    assert t == "**31 issue chưa cập nhật (≥7 ngày) — AI Innovation Projects**"


def test_title_due_soon_includes_days():
    t = rf._title("get_issues_due_soon", {"project_name": "P", "days": 3}, 5)
    assert t == "**5 issue sắp đến hạn (trong 3 ngày) — P**"


def test_title_overdue_and_unassigned_have_no_threshold():
    assert rf._title("get_overdue_issues", {"project_name": "P"}, 2) == "**2 issue quá hạn — P**"
    assert rf._title("get_unassigned_issues", {"project_name": "P"}, 4) == \
        "**4 issue chưa có người phụ trách — P**"


def test_title_list_omits_project_when_absent():
    assert rf._title("list_redmine_issue", {}, 8) == "**8 issue**"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: FAIL — `AttributeError: ... '_title'`

- [ ] **Step 3: Write minimal implementation**

Add to `redmine_format.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph/redmine_format.py tests/meeting/test_redmine_format.py
git commit -m "feat(redmine-format): per-tool Vietnamese header lines"
```

---

## Task 5: Render table + assemble `format_issue_list` (incl. #28815 regression)

**Files:**
- Modify: `meeting/graphs/chat_graph/redmine_format.py`
- Test: `tests/meeting/test_redmine_format.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/meeting/test_redmine_format.py
def _stale_result(rows):
    return {"message": "ok", "total": len(rows), "issues": rows}


def test_format_renders_grouped_table_with_links_and_placeholder():
    rows = [
        {"issue_id": 28815, "subject": "[RnD] Tìm hiểu về AI agent", "status": "New",
         "assigned_to": "hieunq3@vng.com.vn", "last_updated": "2026-06-04",
         "url": "https://pm-db.vng.vn/issues/28815"},
        {"issue_id": 29243, "subject": "Phase 3 - Advanced RAG", "status": "New",
         "assigned_to": None, "last_updated": "2026-06-04",
         "url": "https://pm-db.vng.vn/issues/29243"},
    ]
    out = rf.format_issue_list("get_stale_issues",
                               {"project_name": "AI Innovation Projects", "days": 7},
                               _stale_result(rows))
    assert out.startswith("**2 issue chưa cập nhật (≥7 ngày) — AI Innovation Projects**")
    assert "### New (2)" in out
    assert "[28815](https://pm-db.vng.vn/issues/28815)" in out
    assert "[RnD] Tìm hiểu về AI agent" in out
    assert "| hieunq3 |" in out
    assert "| — |" in out                    # null assignee rendered as placeholder


def test_format_empty_result_is_friendly_message_not_table():
    out = rf.format_issue_list("get_stale_issues", {"project_name": "P", "days": 7},
                               _stale_result([]))
    assert "Không có issue nào" in out
    assert "|" not in out                     # no table rendered


def test_format_returns_none_on_error_or_unknown_shape():
    assert rf.format_issue_list("get_stale_issues", {}, {"error": "redmine mcp error"}) is None
    assert rf.format_issue_list("get_stale_issues", {}, {"message": "weird"}) is None


def test_format_28815_regression_all_31_rows_present_and_populated():
    # 31 rows; #28815 fully populated → every row must appear, none blank.
    rows = []
    for i in range(31):
        iid = 28815 if i == 21 else 30000 + i        # #28815 is the 22nd row (index 21)
        rows.append({
            "issue_id": iid,
            "subject": "[RnD] Tìm hiểu về AI agent" if iid == 28815 else f"issue {iid}",
            "status": "New",
            "assigned_to": "hieunq3@vng.com.vn",
            "last_updated": "2026-06-04",
            "url": f"https://pm-db.vng.vn/issues/{iid}",
        })
    out = rf.format_issue_list("get_stale_issues",
                               {"project_name": "AI Innovation Projects", "days": 7},
                               _stale_result(rows))
    assert "### New (31)" in out
    # body rows = total lines minus title, group heading, and the 2 table-header lines
    body_lines = [ln for ln in out.splitlines() if ln.startswith("| [")]
    assert len(body_lines) == 31
    assert "[28815](https://pm-db.vng.vn/issues/28815) | [RnD] Tìm hiểu về AI agent | hieunq3 | 2026-06-04 |" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: FAIL — `AttributeError: ... 'format_issue_list'`

- [ ] **Step 3: Write minimal implementation**

Add to `redmine_format.py`:

```python
_TABLE_HEADER = "| # | Subject | Assignee | Updated |\n|---|---|---|---|"


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph/redmine_format.py tests/meeting/test_redmine_format.py
git commit -m "feat(redmine-format): render grouped table + format_issue_list assembly

Includes the #28815 regression: a 31-row result renders all 31 rows with
#28815 fully populated."
```

---

## Task 6: Short-circuit in `agent_tools` + routing + builder wiring

**Files:**
- Modify: `meeting/graphs/chat_graph/agent.py` (`agent_tools` body; `route_after_agent_tools`)
- Modify: `meeting/graphs/chat_graph/builder.py:64-68` (edge map)
- Test: `tests/meeting/test_redmine_format.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/meeting/test_redmine_format.py
import asyncio
import json
import uuid

from meeting.graphs.chat_graph import make_agent_tools

_UID = "22222222-2222-2222-2222-222222222222"
_SESSION = object()           # execute_tool is faked, so the session is unused


class _FakeToolset:
    """Minimal DI toolset: get_tool() returns specs, execute_tool() returns canned results."""

    def __init__(self, specs, results):
        self._specs = specs
        self._results = results

    def get_tool(self, name):
        return self._specs.get(name)

    async def execute_tool(self, name, args, *, session, user_id):
        return self._results.get(name, {"status": "ok"})


def _assistant(tool_calls):
    return {"role": "assistant", "content": None, "tool_calls": tool_calls}


def _call(name, args):
    return {"id": f"tc_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


_STALE_SPEC = {"name": "get_stale_issues", "side_effect": False,
               "schema": {"type": "object", "properties": {
                   "project_name": {"type": "string"}, "days": {"type": "integer"}}}}
_RETRIEVE_SPEC = {"name": "retrieve", "side_effect": False,
                  "schema": {"type": "object", "properties": {"query": {"type": "string"}}}}

_STALE_RESULT = {"total": 1, "issues": [
    {"issue_id": 28815, "subject": "[RnD] Tìm hiểu về AI agent", "status": "New",
     "assigned_to": "hieunq3@vng.com.vn", "last_updated": "2026-06-04",
     "url": "https://pm-db.vng.vn/issues/28815"}]}


def _run_agent_tools(specs, results, tool_calls):
    ts = _FakeToolset(specs, results)
    node = make_agent_tools(_SESSION, tools=ts)
    state = {
        "agent_messages": [_assistant(tool_calls)],
        "resolved_meeting_id": None,
        "user_id": _UID,
    }
    return asyncio.run(node(state))


def test_agent_tools_shortcircuits_pure_formattable_read():
    out = _run_agent_tools(
        {"get_stale_issues": _STALE_SPEC}, {"get_stale_issues": _STALE_RESULT},
        [_call("get_stale_issues", {"project_name": "AI Innovation Projects", "days": 7})],
    )
    assert out["agent_route"] == "done"
    assert "[RnD] Tìm hiểu về AI agent" in out["final_reply"]
    assert out["tool_result"]["via"] == "redmine_read"


def test_agent_tools_mixed_round_does_not_shortcircuit():
    out = _run_agent_tools(
        {"get_stale_issues": _STALE_SPEC, "retrieve": _RETRIEVE_SPEC},
        {"get_stale_issues": _STALE_RESULT, "retrieve": {"hits": []}},
        [_call("get_stale_issues", {"project_name": "P", "days": 7}),
         _call("retrieve", {"query": "x"})],
    )
    assert out["agent_route"] == "agent"
    assert "final_reply" not in out


def test_agent_tools_error_result_does_not_shortcircuit():
    out = _run_agent_tools(
        {"get_stale_issues": _STALE_SPEC},
        {"get_stale_issues": {"error": "redmine mcp error: timeout"}},
        [_call("get_stale_issues", {"project_name": "P", "days": 7})],
    )
    assert out["agent_route"] == "agent"
    assert "final_reply" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -k agent_tools -q`
Expected: FAIL — `test_agent_tools_shortcircuits_pure_formattable_read` asserts `out["agent_route"] == "done"` but the current node returns `"agent"` (no short-circuit yet).

- [ ] **Step 3a: Add the import to `agent.py`**

Near the other chat_graph imports at the top of `meeting/graphs/chat_graph/agent.py`, add:

```python
from meeting.graphs.chat_graph.redmine_format import format_issue_list, is_formattable
```

- [ ] **Step 3b: Capture executed read calls in the `agent_tools` loop**

In `agent.py`, in `agent_tools`, initialize an accumulator alongside `pending`/`switched` (currently around line 252):

```python
        pending = None
        switched = None
        executed: list[tuple[str, dict, object]] = []   # (name, args, result) for read calls
```

Then, in the read-tool branch, record the result just before appending the tool message (the block currently ending at `messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _json(result)})`):

```python
            if name == "switch_meeting" and isinstance(result, dict) and result.get("meeting_id"):
                switched = result["meeting_id"]
            executed.append((name, args, result))        # NEW
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": _json(result)})
```

- [ ] **Step 3c: Add the short-circuit when building `out`**

Replace the `out` assembly block at the end of `agent_tools` (currently):

```python
        out: dict = {"agent_messages": messages}
        if switched:
            out["resolved_meeting_id"] = switched
        if pending:
            out["pending_tool"] = pending
            out["agent_route"] = "approve"
        else:
            out["agent_route"] = "agent"
        return out
```

with:

```python
        out: dict = {"agent_messages": messages}
        if switched:
            out["resolved_meeting_id"] = switched
        if pending:
            out["pending_tool"] = pending
            out["agent_route"] = "approve"
        elif (
            executed
            and len(executed) == len(tool_calls)
            and all(is_formattable(name) for name, _, _ in executed)
        ):
            # Pure read-display round: render the table(s) in code and finish the
            # turn — the LLM never re-renders the rows (the #28815 fix). Any render
            # that can't parse its result (None) makes us fall back to the LLM.
            renders = [format_issue_list(name, args, result) for name, args, result in executed]
            if all(r is not None for r in renders):
                out["final_reply"] = "\n\n".join(renders)
                out["tool_result"] = {
                    "status": "ok", "via": "redmine_read",
                    "tools": [name for name, _, _ in executed],
                }
                out["agent_route"] = "done"
            else:
                out["agent_route"] = "agent"
        else:
            out["agent_route"] = "agent"
        return out
```

- [ ] **Step 3d: Extend `route_after_agent_tools`**

Replace (around line 430):

```python
def route_after_agent_tools(state: ChatState) -> Literal["agent", "agent_approve"]:
    return "agent_approve" if state.get("agent_route") == "approve" else "agent"
```

with:

```python
def route_after_agent_tools(
    state: ChatState,
) -> Literal["agent", "agent_approve", "save_reply"]:
    route = state.get("agent_route")
    if route == "approve":
        return "agent_approve"
    if route == "done":
        return "save_reply"
    return "agent"
```

- [ ] **Step 3e: Add the edge in `builder.py`**

In `meeting/graphs/chat_graph/builder.py`, the `agent_tools` conditional edges (lines 64-68) — add the `save_reply` target:

```python
    g.add_conditional_edges(
        "agent_tools",
        route_after_agent_tools,
        {"agent": "agent", "agent_approve": "agent_approve", "save_reply": "save_reply"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/meeting/test_redmine_format.py -q`
Expected: PASS (22 tests)

Then run the chat-graph suite to confirm no regression in the agent loop / existing routing:

Run: `venv/bin/pytest tests/meeting/test_agent_loop.py tests/meeting/test_redmine_apply.py tests/meeting/test_reconcile_bridge.py -q`
Expected: PASS (existing counts unchanged — these never call a formattable read, so they still route `agent`/`agent_approve`).

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph/agent.py meeting/graphs/chat_graph/builder.py tests/meeting/test_redmine_format.py
git commit -m "feat(chat): short-circuit pure Redmine read rounds to deterministic table

agent_tools renders issue-list reads in code and routes straight to
save_reply (agent_route='done'), skipping the LLM render round that mangled
large tables (#28815). Mixed/side-effect rounds and unparseable results fall
back to the LLM unchanged."
```

---

## Final verification

- [ ] **Run the full meeting test suite**

Run: `venv/bin/pytest tests/meeting/ -q`
Expected: PASS — previous green count (per memory, 206) + 22 new = 228, none failing.

---

## Self-review notes (spec coverage)

- Scope (5 tools, one renderer) → Tasks 1 & 5 (`FORMATTABLE_READ_TOOLS`, shared `_render_table`). ✓
- Short-circuit when every call is a formattable read → Task 6 (`all(is_formattable...)` + `len(executed)==len(tool_calls)`). ✓
- `None`/error/empty handling → Tasks 1 & 5 (`_extract_rows` None, friendly empty message). ✓
- Null field → `—` → Task 2. ✓
- Group by status (canonical) + oldest-first → Task 3. ✓
- Per-tool headers w/ project + days → Task 4. ✓
- Routing + builder edge → Task 6 (steps 3d, 3e). ✓
- #28815 regression test → Task 5. ✓
- Known tradeoff (`list_redmine_issue` alone short-circuits a find-then-act) is a documented behavior, not a code path to test.

**Minor deviation from spec:** `tool_result` omits the `count` field (spec showed `count:N`) to avoid coupling `agent.py` to the formatter's private row-extraction; it keeps `status`, `via`, and `tools`. No behavioral impact.
