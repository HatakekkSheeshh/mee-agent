"""Dynamic registration of Redmine MCP tools (offline — fake schemas).

Verifies the registration LOGIC: write/read classification, side_effect flags,
schema pass-through, and that the registered executor proxies to the MCP client.
Live per-tool schemas are checked separately by scripts/probe_redmine_mcp.py.
"""
from __future__ import annotations

import meeting.services.tools as tools
from meeting.services.tools import redmine

# Mirrors a representative slice of the live ~15-tool surface (reads + writes).
FAKE_SCHEMAS = [
    {"name": "get_redmine_projects", "description": "list projects",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_field_metadata", "description": "field metadata",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_overdue_issues", "description": "overdue",
     "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}}}},
    {"name": "get_workload_by_assignee", "description": "workload",
     "inputSchema": {"type": "object", "properties": {"project_name": {"type": "string"}}}},
    {"name": "list_redmine_issue", "description": "list issues",
     "inputSchema": {"type": "object",
                     "properties": {"project_name": {"type": "string"}},
                     "required": ["project_name"]}},
    {"name": "create_redmine_issue", "description": "create",
     "inputSchema": {"type": "object", "properties": {"subject": {"type": "string"}}}},
    {"name": "update_redmine_issue", "description": "update",
     "inputSchema": {"type": "object", "properties": {"issue_id": {"type": "integer"}}}},
    {"name": "bulk_update_issues", "description": "bulk",
     "inputSchema": {"type": "object", "properties": {}}},
]

_FAKE_NAMES = [s["name"] for s in FAKE_SCHEMAS]


def teardown_function():
    # Keep the global TOOLS registry clean so other test modules that assert on
    # the tool set aren't polluted by our dynamic registrations.
    for n in _FAKE_NAMES:
        tools.TOOLS.pop(n, None)


def test_is_write_tool_explicit_set():
    assert redmine.is_write_tool("create_redmine_issue")
    assert redmine.is_write_tool("update_redmine_issue")
    assert redmine.is_write_tool("bulk_update_issues")


def test_is_write_tool_conservative_heuristic():
    # A mutating verb NOT in the explicit set is still gated (default-deny).
    assert redmine.is_write_tool("delete_redmine_issue")
    assert redmine.is_write_tool("close_issue")


def test_is_write_tool_reads_are_not_writes():
    for n in ("get_redmine_projects", "get_field_metadata", "get_overdue_issues",
              "get_workload_by_assignee", "list_redmine_issue"):
        assert not redmine.is_write_tool(n), n


def test_register_returns_all_names():
    names = redmine.register_redmine_tools(FAKE_SCHEMAS)
    assert set(names) == set(_FAKE_NAMES)


def test_register_marks_writes_side_effect_reads_not():
    redmine.register_redmine_tools(FAKE_SCHEMAS)
    for n in ("create_redmine_issue", "update_redmine_issue", "bulk_update_issues"):
        assert tools.get_tool(n)["side_effect"] is True, n
    for n in ("get_redmine_projects", "get_field_metadata", "get_overdue_issues",
              "get_workload_by_assignee", "list_redmine_issue"):
        assert tools.get_tool(n)["side_effect"] is False, n


def test_register_carries_schema_through():
    redmine.register_redmine_tools(FAKE_SCHEMAS)
    schema = tools.get_tool("list_redmine_issue")["schema"]
    assert schema["required"] == ["project_name"]
    assert schema["properties"]["project_name"]["type"] == "string"


def test_register_skips_nameless_entry():
    names = redmine.register_redmine_tools([{"description": "no name"}])
    assert names == []


def test_missing_input_schema_falls_back_to_empty_object():
    redmine.register_redmine_tools([{"name": "get_overdue_issues", "description": "x"}])
    assert tools.get_tool("get_overdue_issues")["schema"] == {"type": "object", "properties": {}}


async def test_registered_executor_proxies_to_client(monkeypatch):
    redmine.register_redmine_tools(FAKE_SCHEMAS)
    captured = {}

    class _FakeClient:
        async def call_tool(self, name, arguments):
            captured["name"] = name
            captured["args"] = arguments
            return {"ok": True}

    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _FakeClient())
    # Call the executor directly (not execute_tool) to skip audit-logging/DB.
    executor = tools.get_tool("get_overdue_issues")["executor"]
    out = await executor({"project_name": "GIP"}, session=None, user_id=None)
    assert out == {"ok": True}
    assert captured == {"name": "get_overdue_issues", "args": {"project_name": "GIP"}}
