"""create_task → MCP apply: item→args mapping, summary, and full-graph flow."""
from __future__ import annotations

from meeting.graphs import _chat_serde as serde


# ── Task 4: pure item→Redmine-args helpers ──────────────────────────
# LIVE-SCHEMA CORRECTION (probe 2026-06-12): create_redmine_issue AND
# update_redmine_issue both expose `due_date` as a real field, so it is passed
# DIRECTLY (not folded into description/notes as the original plan assumed).
def test_create_args_defaults_tracker_and_passes_due_date_directly():
    args = serde.redmine_create_args(
        "GIP",
        {"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01/2026", "description": "schema"},
    )
    assert args["project_name"] == "GIP"
    assert args["subject"] == "viết migration"
    assert args["tracker"] == "Task"            # default when item has none
    assert args["assigned_to"] == "Hiếu"
    assert args["description"] == "schema"      # due_date NOT folded in
    assert args["due_date"] == "10/01/2026"     # passed as a real field


def test_create_args_respects_explicit_tracker():
    args = serde.redmine_create_args("GIP", {"subject": "x", "tracker": "Bug"})
    assert args["tracker"] == "Bug"


def test_create_args_omits_absent_optionals():
    args = serde.redmine_create_args("GIP", {"subject": "x", "assignee": "Mai"})
    assert "due_date" not in args
    assert "description" not in args


def test_update_args_includes_only_present_fields():
    args = serde.redmine_update_args("GIP", {"subject": "new", "due_date": "12/01"}, "123")
    assert args["issue_id"] == "123"
    assert args["project_name"] == "GIP"
    assert args["subject"] == "new"
    assert args["due_date"] == "12/01"          # direct field, not folded
    assert "assigned_to" not in args            # absent in item → omitted
    assert "notes" not in args                  # no description → no note


def test_update_args_maps_description_to_notes():
    args = serde.redmine_update_args("GIP", {"description": "đã làm xong phần A"}, "9")
    assert args["notes"] == "đã làm xong phần A"


def test_summary_counts_ok_and_lists_failures():
    results = [
        {"subject": "a", "result": {"id": 1}},
        {"subject": "b", "result": {"error": "no assignee"}},
    ]
    text = serde.summarize_redmine_apply("GIP", results)
    assert "1/2" in text
    assert "GIP" in text
    assert "b" in text and "no assignee" in text


# ── Task 5: full-graph create_task → MCP apply ──────────────────────
import uuid
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from meeting.graphs import chat_graph
from meeting.graphs.chat_graph import (
    ChatState,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_execute,
    route_after_agent_tools,
)

MID = "11111111-1111-1111-1111-111111111111"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION = object()


def _resp_tool(calls):
    tcs = [SimpleNamespace(id=c["id"], type="function",
                           function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
           for c in calls]
    msg = SimpleNamespace(content=None, tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


class _FakeLLM:
    def __init__(self, scripted):
        self._s = list(scripted); self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        i = len(self.calls); self.calls.append(kw); return self._s[i]


class _FakeExec:
    def __init__(self): self.calls = []
    async def __call__(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args})
        return {"id": 100 + len(self.calls)}   # fake created issue id, no "error"


CREATE_TASK_SPEC = {
    "name": "create_task", "description": "make tasks", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "title": {"type": "string"}}},
}


class FakeToolset:
    def __init__(self): self.exec = _FakeExec()
    def list_tools(self): return [CREATE_TASK_SPEC]
    def get_tool(self, n): return CREATE_TASK_SPEC if n == "create_task" else None
    async def execute_tool(self, name, args, *, session, user_id):
        return await self.exec(name, args, session=session, user_id=user_id)
    def build_task_items(self, items, *, description: str = ""):
        from meeting.services import build_task_items as real
        return real(items, description=description)


def _build(llm, checkpointer, tools):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm, tools=tools))
    g.add_node("agent_tools", make_agent_tools(SESSION, tools=tools))
    g.add_node("agent_approve", make_agent_approve(tools=tools))
    g.add_node("agent_execute", make_agent_execute(SESSION, tools=tools))
    g.add_node("save_reply", lambda s: {})
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route_after_agent,
                            {"agent_tools": "agent_tools", "save_reply": "save_reply"})
    g.add_conditional_edges("agent_tools", route_after_agent_tools,
                            {"agent": "agent", "agent_approve": "agent_approve"})
    g.add_edge("agent_approve", "agent_execute")
    g.add_conditional_edges("agent_execute", route_after_agent_execute,
                            {"agent": "agent", "save_reply": "save_reply"})
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _initial(msg):
    return {"session_id": "s", "user_id": str(UID), "user_message": msg,
            "resolved_meeting_id": MID,
            "meeting_context": {"id": MID, "title": "AI Innovation Project"}}


async def _interrupted(graph, cfg):
    snap = await graph.aget_state(cfg); return bool(snap.next)


async def test_create_task_applies_over_mcp(monkeypatch):
    ts = FakeToolset()

    async def fake_items(session, mid):
        return [
            {"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"},
            {"pic": "Mai", "deadline": "", "item": "POC caching"},
        ]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([_resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}])])
    graph = _build(llm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "apply"}}

    # Turn 1: agent calls create_task → HITL interrupt (the only approval)
    await graph.ainvoke(_initial("đồng bộ các việc trong họp lên Redmine"), cfg)
    assert await _interrupted(graph, cfg)
    assert ts.exec.calls == []   # nothing applied before approval

    # Approve → apply over MCP (one create_redmine_issue per item), terminal
    result = await graph.ainvoke(Command(resume={"action": "approved"}), cfg)
    assert not await _interrupted(graph, cfg)
    applied = [c for c in ts.exec.calls if c["name"] == "create_redmine_issue"]
    assert len(applied) == 2
    assert {c["args"]["subject"] for c in applied} == {"viết migration", "POC caching"}
    assert applied[0]["args"]["tracker"] == "Task"
    assert "2/2" in result["final_reply"]
    assert result["tool_result"]["status"] == "redmine_apply"


async def test_create_task_update_item_uses_update_tool(monkeypatch):
    """An item carrying an issue_id is reconciled via update_redmine_issue, not
    a fresh create (defensive/future-proof apply branch)."""
    ts = FakeToolset()

    llm = _FakeLLM([_resp_tool([{"id": "c1", "name": "create_task",
                                 "arguments": '{"title": "deploy v1"}'}])])
    graph = _build(llm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "apply-update"}}

    await graph.ainvoke(_initial("tạo task deploy v1"), cfg)
    # Edit the single item on the card to carry an existing issue_id → update path.
    result = await graph.ainvoke(
        Command(resume={"action": "approved", "edited_args": {
            "project": "GIP",
            "items": [{"subject": "deploy v1", "assignee": "Mai", "issue_id": "555"}],
        }}),
        cfg,
    )
    assert not await _interrupted(graph, cfg)
    updates = [c for c in ts.exec.calls if c["name"] == "update_redmine_issue"]
    assert len(updates) == 1
    assert updates[0]["args"]["issue_id"] == "555"
    assert updates[0]["args"]["project_name"] == "GIP"
    assert "1/1" in result["final_reply"]


async def test_reject_create_task_applies_nothing(monkeypatch):
    ts = FakeToolset()

    async def fake_items(session, mid):
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([_resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}])])
    graph = _build(llm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "apply-reject"}}

    await graph.ainvoke(_initial("đồng bộ lên Redmine"), cfg)
    result = await graph.ainvoke(Command(resume={"action": "rejected", "reason": "thôi"}), cfg)
    assert not await _interrupted(graph, cfg)
    assert ts.exec.calls == []
    assert result["final_reply"] == chat_graph.REJECT_REPLY


# ── Task 8: agent system-prompt Redmine guidance ────────────────────
def test_agent_prompt_has_redmine_guidance():
    prompt = chat_graph._agent_system_prompt({"meeting_context": {"title": "GIP"}})
    assert "list_redmine_issue" in prompt
    assert "update_redmine_issue" in prompt
    # create_task vs create_redmine_issue disambiguation present
    assert "create_redmine_issue" in prompt and "create_task" in prompt
