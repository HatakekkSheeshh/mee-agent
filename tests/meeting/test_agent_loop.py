"""Task 4 — the unified tool-calling agent loop (Path A: native tool-calling).

Assembles a minimal LangGraph from the real agent nodes (agent ⇄ agent_tools,
agent_approve interrupt, agent_execute) with an in-memory checkpointer, an
injected FakeLLM (scripted tool_calls / final text), and monkeypatched tool
plumbing (list_tools/get_tool/execute_tool). Proves:
  - answer-only (no tool) completes,
  - auto-retrieve-then-answer (read tool auto-runs, meeting_id injected),
  - side-effect tool interrupts, then resumes & executes on approve,
  - reject does not execute and still replies,
  - max-rounds cap terminates with a reply,
  - switch_meeting re-scopes subsequent retrieval,
  - replay-safety: the side-effect tool executes exactly once.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from meeting.graphs.chat_graph import (
    ChatState,
    MAX_AGENT_ROUNDS,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_tools,
)

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION = object()  # execute_tool is faked, so the session is never used


# ─── fake OpenAI client ──────────────────────────────────────────────

def text(content: str):
    return {"kind": "text", "content": content}


def tool(tool_calls: list[dict]):
    return {"kind": "tools", "tool_calls": tool_calls}


def _to_response(spec: dict):
    if spec["kind"] == "text":
        msg = SimpleNamespace(content=spec["content"], tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    tcs = [
        SimpleNamespace(
            id=t["id"], type="function",
            function=SimpleNamespace(name=t["name"], arguments=t["arguments"]),
        )
        for t in spec["tool_calls"]
    ]
    msg = SimpleNamespace(content=None, tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


class FakeLLM:
    """Scripted OpenAI-style client. Returns responses in order; records calls."""

    def __init__(self, scripted, *, repeat_last=False):
        self._scripted = list(scripted)
        self.repeat_last = repeat_last
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        idx = len(self.calls)
        self.calls.append(kwargs)
        if idx < len(self._scripted):
            return _to_response(self._scripted[idx])
        if self.repeat_last and self._scripted:
            return _to_response(self._scripted[-1])
        raise AssertionError(f"FakeLLM: no scripted response for call {idx}")


# ─── fake tool plumbing ──────────────────────────────────────────────

RETRIEVE_SPEC = {
    "name": "retrieve", "description": "search meeting", "side_effect": False,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "query": {"type": "string"}},
               "required": ["meeting_id", "query"]},
}
CREATE_SPEC = {
    "name": "create_task", "description": "make a task", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "title": {"type": "string"}}},
}
SWITCH_SPEC = {
    "name": "switch_meeting", "description": "switch project by title", "side_effect": False,
    "schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
}
SEND_SPEC = {
    "name": "send_email", "description": "send email", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "to": {"type": "string"}}},
}
_SPECS = {s["name"]: s for s in (RETRIEVE_SPEC, CREATE_SPEC, SWITCH_SPEC, SEND_SPEC)}


class FakeTools:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls: list[dict] = []

    async def __call__(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args})
        return self.results.get(name, {"status": "ok"})


class FakeToolset:
    """Injected tool bundle (DI seam) — replaces patching chat_graph globals."""

    def __init__(self, specs, exec_results=None):
        self._specs = specs
        self.exec = FakeTools(exec_results)

    @property
    def calls(self):
        return self.exec.calls

    def list_tools(self):
        return list(self._specs.values())

    def get_tool(self, n):
        return self._specs.get(n)

    async def execute_tool(self, name, args, *, session, user_id):
        return await self.exec(name, args, session=session, user_id=user_id)

    def build_task_items(self, items):
        from meeting.services import build_task_items as real
        return real(items)


def _install(monkeypatch=None, exec_results=None) -> FakeToolset:
    """Build a fake toolset to inject via `tools=`. (monkeypatch kept for
    call-site compatibility; nothing is patched anymore.)"""
    return FakeToolset(_SPECS, exec_results)


# ─── minimal graph from the real agent nodes ─────────────────────────

def _build(llm, checkpointer, tools):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm, tools=tools))
    g.add_node("agent_tools", make_agent_tools(SESSION, tools=tools))
    g.add_node("agent_approve", make_agent_approve(tools=tools))
    g.add_node("agent_execute", make_agent_execute(SESSION, tools=tools))
    g.add_node("save_reply", lambda state: {})
    g.set_entry_point("agent")
    g.add_conditional_edges(
        "agent", route_after_agent,
        {"agent_tools": "agent_tools", "save_reply": "save_reply"},
    )
    g.add_conditional_edges(
        "agent_tools", route_after_agent_tools,
        {"agent": "agent", "agent_approve": "agent_approve"},
    )
    g.add_edge("agent_approve", "agent_execute")
    g.add_edge("agent_execute", "agent")
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _config(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _initial(user_message, meeting_id="bound-mid"):
    return {
        "session_id": "s", "user_id": str(UID), "user_message": user_message,
        "resolved_meeting_id": meeting_id,
        "meeting_context": {"id": meeting_id, "title": "Dự án Mee"},
    }


async def _interrupted(graph, config) -> bool:
    snap = await graph.aget_state(config)
    return bool(snap.next)


async def _interrupt_value(graph, config):
    snap = await graph.aget_state(config)
    for task in snap.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


# ─── tests ───────────────────────────────────────────────────────────

async def test_agent_answer_only(monkeypatch):
    ts = _install(monkeypatch)
    llm = FakeLLM([text("Mình là Mee, trợ lý cuộc họp.")])
    graph = _build(llm, MemorySaver(), ts)
    cfg = _config("answer-only")

    result = await graph.ainvoke(_initial("Bạn là ai?"), cfg)

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Mình là Mee, trợ lý cuộc họp."
    assert len(llm.calls) == 1


async def test_agent_auto_retrieve_then_answer(monkeypatch):
    ft = _install(monkeypatch, {"retrieve": {"status": "ok", "chunks": [{"text": "deploy v1 thứ 6"}]}})
    llm = FakeLLM([
        tool([{"id": "c1", "name": "retrieve", "arguments": '{"query": "deploy v1"}'}]),
        text("Cuộc họp quyết định deploy v1 vào thứ 6."),
    ])
    graph = _build(llm, MemorySaver(), ft)
    cfg = _config("auto-retrieve")

    result = await graph.ainvoke(_initial("deploy v1 thế nào?"), cfg)

    assert not await _interrupted(graph, cfg)
    assert "deploy v1" in result["final_reply"]
    assert ft.calls[0]["name"] == "retrieve"
    # meeting_id auto-injected from resolved_meeting_id
    assert ft.calls[0]["args"]["meeting_id"] == "bound-mid"
    assert ft.calls[0]["args"]["query"] == "deploy v1"
    assert len(llm.calls) == 2


async def test_agent_side_effect_interrupts_then_executes(monkeypatch):
    ft = _install(monkeypatch, {"send_email": {"status": "sent_mock"}})
    llm = FakeLLM([
        tool([{"id": "c1", "name": "send_email", "arguments": '{"to": "a@x.vn"}'}]),
        text("Đã gửi email."),
    ])
    graph = _build(llm, MemorySaver(), ft)
    cfg = _config("side-effect")

    await graph.ainvoke(_initial("gửi email"), cfg)

    assert await _interrupted(graph, cfg)
    pending = await _interrupt_value(graph, cfg)
    assert pending["tool"] == "send_email"
    assert pending["args"]["to"] == "a@x.vn"
    assert pending["args"]["meeting_id"] == "bound-mid"
    assert ft.calls == []  # not executed before approval

    result = await graph.ainvoke(Command(resume={"action": "approved"}), cfg)

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã gửi email."
    assert len(ft.calls) == 1
    assert ft.calls[0]["name"] == "send_email"
    assert len(llm.calls) == 2


async def test_agent_side_effect_rejected(monkeypatch):
    ft = _install(monkeypatch)
    llm = FakeLLM([
        tool([{"id": "c1", "name": "send_email", "arguments": '{"to": "a@x.vn"}'}]),
        text("OK, mình không gửi nữa."),
    ])
    graph = _build(llm, MemorySaver(), ft)
    cfg = _config("rejected")

    await graph.ainvoke(_initial("gửi email"), cfg)
    assert await _interrupted(graph, cfg)

    result = await graph.ainvoke(Command(resume={"action": "rejected", "reason": "thôi"}), cfg)

    assert not await _interrupted(graph, cfg)
    assert ft.calls == []  # never executed
    assert result["final_reply"] == "OK, mình không gửi nữa."
    assert len(llm.calls) == 2


async def test_agent_max_rounds_cap(monkeypatch):
    ts = _install(monkeypatch, {"retrieve": {"status": "ok", "chunks": []}})
    llm = FakeLLM(
        [tool([{"id": "cN", "name": "retrieve", "arguments": '{"query": "x"}'}])],
        repeat_last=True,
    )
    graph = _build(llm, MemorySaver(), ts)
    cfg = _config("max-rounds")

    result = await graph.ainvoke(_initial("loop forever"), cfg)

    assert not await _interrupted(graph, cfg)
    assert result.get("final_reply")
    # Cap stops further LLM calls at MAX_AGENT_ROUNDS.
    assert len(llm.calls) == MAX_AGENT_ROUNDS


async def test_agent_switch_meeting_rescopes_retrieval(monkeypatch):
    ft = _install(monkeypatch, {
        "switch_meeting": {"status": "ok", "meeting_id": "other-mid",
                           "candidates": [{"id": "other-mid", "title": "Dự án Khác"}]},
        "retrieve": {"status": "ok", "chunks": [{"text": "giai đoạn 2"}]},
    })
    llm = FakeLLM([
        tool([{"id": "c1", "name": "switch_meeting", "arguments": '{"title": "Dự án Khác"}'}]),
        tool([{"id": "c2", "name": "retrieve", "arguments": '{"query": "trạng thái"}'}]),
        text("Dự án Khác đang ở giai đoạn 2."),
    ])
    graph = _build(llm, MemorySaver(), ft)
    cfg = _config("switch")

    result = await graph.ainvoke(_initial("dự án khác sao rồi"), cfg)

    assert not await _interrupted(graph, cfg)
    retrieve_calls = [c for c in ft.calls if c["name"] == "retrieve"]
    assert retrieve_calls[0]["args"]["meeting_id"] == "other-mid"
    assert "giai đoạn 2" in result["final_reply"]
