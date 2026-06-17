"""create_task → pm-agent reconcile bridge (2026-06-08 spec)."""
from __future__ import annotations

import uuid

from meeting.graphs import chat_graph


def test_reconcile_text_lists_items_and_project():
    text = chat_graph._reconcile_text("GIP", [
        {"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01/2026"},
        {"subject": "POC caching", "assignee": "", "due_date": ""},
    ])
    assert "GIP" in text
    assert "viết migration" in text
    assert "Hiếu" in text
    assert "10/01/2026" in text
    assert "POC caching" in text


def test_reconcile_text_handles_blank_project():
    text = chat_graph._reconcile_text("", [{"subject": "x"}])
    assert "x" in text
    assert text  # non-empty even with no project


MID = "11111111-1111-1111-1111-111111111111"


async def test_build_template_from_mom_action_items(monkeypatch):
    async def fake_items(session, mid):
        assert mid == uuid.UUID(MID)
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]

    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    tpl = await chat_graph._build_reconcile_template(
        object(), {}, {"title": "AI Innovation Project"}, MID
    )
    assert tpl["project"] == "AI Innovation Project"   # default = meeting title
    assert tpl["items"][0]["subject"] == "viết migration"
    assert tpl["items"][0]["assignee"] == "Hiếu"


async def test_build_template_from_explicit_title():
    tpl = await chat_graph._build_reconcile_template(
        object(),
        {"title": "Deploy v1", "assignee": "Mai", "deadline": "06/06/2026"},
        {"title": "Dự án Mee"}, MID,
    )
    assert tpl["project"] == "Dự án Mee"
    assert len(tpl["items"]) == 1
    assert tpl["items"][0]["subject"] == "Deploy v1"
    assert tpl["items"][0]["due_date"] == "06/06/2026"


async def test_build_template_filters_by_assignee(monkeypatch):
    async def fake_items(session, mid):
        return [
            {"pic": "Duy Anh", "deadline": "10/01", "item": "viết migration"},
            {"pic": "Hiếu", "deadline": "11/01", "item": "POC caching"},
            {"pic": "Duy Anh", "deadline": "12/01", "item": "review PR"},
        ]

    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    tpl = await chat_graph._build_reconcile_template(
        object(), {"assignee": "duy anh"}, {"title": "GIP"}, MID
    )
    assert tpl["project"] == "GIP"
    assert len(tpl["items"]) == 2  # only Duy Anh's items (case-insensitive)
    assert {it["subject"] for it in tpl["items"]} == {"viết migration", "review PR"}
    assert all(it["assignee"] == "Duy Anh" for it in tpl["items"])


def test_route_after_agent_execute_reconcile_goes_to_pm():
    assert chat_graph.route_after_agent_execute({"agent_route": "reconcile"}) == "pm_call"


def test_route_after_agent_execute_default_goes_to_agent():
    assert chat_graph.route_after_agent_execute({"agent_route": "agent"}) == "agent"
    assert chat_graph.route_after_agent_execute({}) == "agent"


from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from meeting.graphs.chat_graph import (
    ChatState,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    make_pm_call,
    pm_await,
    pm_reply,
    route_after_agent,
    route_after_agent_execute,
    route_after_agent_tools,
    route_after_pm_call,
)
from meeting.services.pm_agent_client import PmAgentResult

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION = object()


def _resp_text(content):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def _resp_tool(calls):
    tcs = [SimpleNamespace(id=c["id"], type="function",
                           function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
           for c in calls]
    msg = SimpleNamespace(content=None, tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


class _FakeLLM:
    def __init__(self, scripted):
        self._s = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        i = len(self.calls); self.calls.append(kw); return self._s[i]


class _FakePm:
    def __init__(self, results):
        self._r = list(results); self.calls = []

    async def send_message(self, text, *, task_id=None, context_id=None, data_part=None, bearer=None):
        self.calls.append({"text": text, "task_id": task_id,
                           "context_id": context_id, "data_part": data_part, "bearer": bearer})
        return self._r[len(self.calls) - 1]

    async def cancel(self, task_id):
        pass


CREATE_SPEC_B = {
    "name": "create_task", "description": "make tasks", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "title": {"type": "string"}}},
}


class _FakeExec:
    def __init__(self): self.calls = []
    async def __call__(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args}); return {"status": "ok"}


class FakeToolset:
    """Injected tool bundle (DI seam) — replaces patching chat_graph globals.
    build_task_items is the REAL one so _build_reconcile_template converts MoM
    action_items exactly as in production."""

    def __init__(self, specs):
        self._specs = specs
        self.exec = _FakeExec()

    def list_tools(self):
        return list(self._specs.values())

    def get_tool(self, n):
        return self._specs.get(n)

    async def execute_tool(self, name, args, *, session, user_id):
        return await self.exec(name, args, session=session, user_id=user_id)

    def build_task_items(self, items):
        from meeting.services import build_task_items as real
        return real(items)


def _toolset_b():
    return FakeToolset({"create_task": CREATE_SPEC_B})


def _build_full(llm, pm_client, checkpointer, tools):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm, tools=tools))
    g.add_node("agent_tools", make_agent_tools(SESSION, tools=tools))
    g.add_node("agent_approve", make_agent_approve(tools=tools))
    g.add_node("agent_execute", make_agent_execute(SESSION, tools=tools))
    g.add_node("pm_call", make_pm_call(pm_client))
    g.add_node("pm_await", pm_await)
    g.add_node("pm_reply", pm_reply)
    g.add_node("save_reply", lambda s: {})
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route_after_agent,
                            {"agent_tools": "agent_tools", "save_reply": "save_reply"})
    g.add_conditional_edges("agent_tools", route_after_agent_tools,
                            {"agent": "agent", "agent_approve": "agent_approve"})
    g.add_edge("agent_approve", "agent_execute")
    g.add_conditional_edges("agent_execute", route_after_agent_execute,
                            {"agent": "agent", "pm_call": "pm_call", "save_reply": "save_reply"})
    g.add_conditional_edges("pm_call", route_after_pm_call,
                            {"pm_await": "pm_await", "pm_reply": "pm_reply", "save_reply": "save_reply"})
    g.add_edge("pm_await", "pm_call")
    g.add_edge("pm_reply", "save_reply")
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _initial_b(msg):
    # resolved_meeting_id must be a real UUID — _build_reconcile_template parses it
    # with uuid.UUID() (in production it is str(meeting.id)). MID is defined above.
    return {"session_id": "s", "user_id": str(UID), "user_message": msg,
            "resolved_meeting_id": MID,
            "meeting_context": {"id": MID, "title": "AI Innovation Project"}}


async def _interrupted_b(graph, cfg):
    snap = await graph.aget_state(cfg); return bool(snap.next)


async def _interrupt_val_b(graph, cfg):
    snap = await graph.aget_state(cfg)
    for t in snap.tasks:
        if t.interrupts:
            return t.interrupts[0].value
    return None


async def test_full_bridge_create_task_to_reconcile(monkeypatch):
    ts = _toolset_b()

    async def fake_items(session, mid):
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([
        _resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}]),
    ])
    pm = _FakePm([
        PmAgentResult("task-1", "input_required", "Xác nhận tạo issue?", True,
                      [{"actions": "CREATE", "subject": "viết migration"}], context_id="ctx-1"),
        PmAgentResult("task-1", "completed", "Đã tạo issue trên Redmine.", False, None, context_id="ctx-1"),
    ])
    graph = _build_full(llm, pm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "bridge"}}

    # Turn 1: agent calls create_task → GATE 1 (local) interrupt
    await graph.ainvoke(_initial_b("đồng bộ các việc trong họp lên Redmine"), cfg)
    assert await _interrupted_b(graph, cfg)
    gate1 = await _interrupt_val_b(graph, cfg)
    assert gate1["tool"] == "create_task"
    assert gate1["args"]["project"] == "AI Innovation Project"   # default from title
    assert gate1["args"]["items"][0]["subject"] == "viết migration"
    assert pm.calls == []   # nothing sent to pm before GATE 1 approval

    # Approve GATE 1 with an edited project → bridges to pm → GATE 2 (pm need_approval)
    await graph.ainvoke(
        Command(resume={"action": "approved", "edited_args": {"project": "GIP"}}), cfg
    )
    assert await _interrupted_b(graph, cfg)
    gate2 = await _interrupt_val_b(graph, cfg)
    assert gate2["kind"] == "need_approval"
    assert len(pm.calls) == 1
    assert "GIP" in pm.calls[0]["text"]                          # edited project used
    assert pm.calls[0]["data_part"]["project"] == "GIP"
    assert pm.calls[0]["data_part"]["items"][0]["subject"] == "viết migration"

    # Approve GATE 2 → pm completes
    result = await graph.ainvoke(Command(resume={"approval_action": "approve"}), cfg)
    assert not await _interrupted_b(graph, cfg)
    assert result["final_reply"] == "Đã tạo issue trên Redmine."
    assert len(pm.calls) == 2


async def test_bridge_reject_gate1_no_handoff(monkeypatch):
    ts = _toolset_b()

    async def fake_items(session, mid):
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    # Reject is terminal: no 2nd LLM turn, so only the first tool-call response is needed.
    llm = _FakeLLM([
        _resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}]),
    ])
    pm = _FakePm([])
    graph = _build_full(llm, pm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "bridge-reject"}}

    await graph.ainvoke(_initial_b("đồng bộ lên Redmine"), cfg)
    assert await _interrupted_b(graph, cfg)

    result = await graph.ainvoke(
        Command(resume={"action": "rejected", "reason": "thôi"}), cfg
    )
    assert not await _interrupted_b(graph, cfg)
    assert pm.calls == []                       # never bridged to pm
    # GATE-1 reject ends the turn with the canned reply (no handoff, no 2nd LLM turn).
    assert result["final_reply"] == chat_graph.REJECT_REPLY


def test_classify_prompt_routes_meeting_tasks_to_agent():
    # The classify system prompt was extracted to CLASSIFY_SYSTEM_PROMPT
    # (re-exported on chat_graph); assert the routing bias lives there.
    src = chat_graph.CLASSIFY_SYSTEM_PROMPT
    assert "biên bản" in src and "agent" in src
    assert "đồng bộ" in src or "lên Redmine" in src
    # The new bias line/example routes meeting-derived task sync to the agent.
    assert "lên Redmine" in src
