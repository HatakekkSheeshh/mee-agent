"""Deterministic Redmine issue-list rendering (pure) + agent_tools short-circuit."""
from __future__ import annotations

import asyncio
import json

from src.graphs.chat_graph import redmine_format as rf
from src.graphs.chat_graph import make_agent_tools


# ── Task 1: scaffold ────────────────────────────────────────────────
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


# ── Task 2: cell helpers ────────────────────────────────────────────
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


# ── Task 3: group + sort ────────────────────────────────────────────
def test_group_and_sort_orders_groups_canonically_and_rows_oldest_first():
    rows = [
        {"issue_id": 1, "status": "In Progress", "last_updated": "2026-06-04"},
        {"issue_id": 2, "status": "New", "last_updated": "2026-06-01"},
        {"issue_id": 3, "status": "New", "last_updated": "2026-05-20"},
        {"issue_id": 4, "status": "Zeta-custom", "last_updated": "2026-06-02"},
    ]
    grouped = rf._group_and_sort(rows)
    assert [status for status, _ in grouped] == ["New", "In Progress", "Zeta-custom"]
    new_ids = [r["issue_id"] for r in grouped[0][1]]
    assert new_ids == [3, 2]


def test_group_and_sort_null_status_and_null_date():
    rows = [
        {"issue_id": 1, "status": None, "last_updated": None},
        {"issue_id": 2, "status": None, "last_updated": "2026-06-01"},
    ]
    grouped = rf._group_and_sort(rows)
    assert grouped[0][0] == "—"
    assert [r["issue_id"] for r in grouped[0][1]] == [2, 1]


# ── Task 4: titles ──────────────────────────────────────────────────
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


# ── Task 5: render + assemble ───────────────────────────────────────
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
    assert "| — |" in out


def test_format_empty_result_is_friendly_message_not_table():
    out = rf.format_issue_list("get_stale_issues", {"project_name": "P", "days": 7},
                               _stale_result([]))
    assert "Không có issue nào" in out
    assert "|" not in out


def test_format_returns_none_on_error_or_unknown_shape():
    assert rf.format_issue_list("get_stale_issues", {}, {"error": "redmine mcp error"}) is None
    assert rf.format_issue_list("get_stale_issues", {}, {"message": "weird"}) is None


def test_format_28815_regression_all_31_rows_present_and_populated():
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
    body_lines = [ln for ln in out.splitlines() if ln.startswith("| [")]
    assert len(body_lines) == 31
    assert "[28815](https://pm-db.vng.vn/issues/28815) | [RnD] Tìm hiểu về AI agent | hieunq3 | 2026-06-04 |" in out


# ── Task 6: agent_tools short-circuit (integration) ─────────────────
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
