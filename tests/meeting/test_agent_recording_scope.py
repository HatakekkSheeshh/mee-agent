"""Option B — agent answers scoped to ONE recording.

No graph change: read tools auto-run in agent_tools. This proves the system
prompt steers the agent to resolve a recording first, and that the generic loop
threads a recording_id from `list_recordings` into `recording_mom` — with
meeting_id auto-injected for list_recordings but NOT for recording_mom
(recording_id is the LLM's job).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from meeting.graphs.chat_graph import (
    ChatState,
    _agent_system_prompt,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_tools,
)

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
RID = "33333333-3333-3333-3333-333333333333"
SESSION = object()


# ─── fakes (mirrors test_agent_loop.py) ───────────────────────────────

def _text(content):
    return {"kind": "text", "content": content}


def _tool(tool_calls):
    return {"kind": "tools", "tool_calls": tool_calls}


def _to_response(spec):
    if spec["kind"] == "text":
        msg = SimpleNamespace(content=spec["content"], tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    tcs = [
        SimpleNamespace(id=t["id"], type="function",
                        function=SimpleNamespace(name=t["name"], arguments=t["arguments"]))
        for t in spec["tool_calls"]
    ]
    msg = SimpleNamespace(content=None, tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


class FakeLLM:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        idx = len(self.calls)
        self.calls.append(kwargs)
        return _to_response(self._scripted[idx])


LIST_SPEC = {
    "name": "list_recordings", "description": "list recordings", "side_effect": False,
    "schema": {"type": "object", "properties": {"meeting_id": {"type": "string"}}},
}
MOM_SPEC = {
    "name": "recording_mom", "description": "read one recording mom", "side_effect": False,
    "schema": {"type": "object", "properties": {"recording_id": {"type": "string"}},
               "required": ["recording_id"]},
}
_SPECS = {s["name"]: s for s in (LIST_SPEC, MOM_SPEC)}


class FakeTools:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def __call__(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args})
        return self.results.get(name, {"status": "ok"})


class FakeToolset:
    """Injected tool bundle (DI seam) — replaces patching chat_graph globals."""

    def __init__(self, specs, results):
        self._specs = specs
        self.exec = FakeTools(results)

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


def _install(monkeypatch, results):
    """Build a fake toolset to inject via `tools=` (nothing is patched anymore)."""
    return FakeToolset(_SPECS, results)


def _build(llm, checkpointer, tools):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm, tools=tools))
    g.add_node("agent_tools", make_agent_tools(SESSION, tools=tools))
    g.add_node("agent_approve", make_agent_approve(tools=tools))
    g.add_node("agent_execute", make_agent_execute(SESSION, tools=tools))
    g.add_node("save_reply", lambda state: {})
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route_after_agent,
                            {"agent_tools": "agent_tools", "save_reply": "save_reply"})
    g.add_conditional_edges("agent_tools", route_after_agent_tools,
                            {"agent": "agent", "agent_approve": "agent_approve"})
    g.add_edge("agent_approve", "agent_execute")
    g.add_edge("agent_execute", "agent")
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _initial(user_message, meeting_id="bound-mid"):
    return {
        "session_id": "s", "user_id": str(UID), "user_message": user_message,
        "resolved_meeting_id": meeting_id,
        "meeting_context": {"id": meeting_id, "title": "AI Innovation Project"},
    }


async def _interrupted(graph, config):
    snap = await graph.aget_state(config)
    return bool(snap.next)


# ─── prompt ───────────────────────────────────────────────────────────

def test_system_prompt_steers_recording_scoped_lookup():
    prompt = _agent_system_prompt(_initial("việc của Hiếu trong Meeting 1"))
    assert "list_recordings" in prompt
    assert "recording_mom" in prompt
    # must warn against cross-recording mis-attribution (Option C mitigation)
    assert "recording" in prompt.lower()


# ─── loop ─────────────────────────────────────────────────────────────

async def test_recording_scoped_flow(monkeypatch):
    ft = _install(monkeypatch, {
        "list_recordings": {
            "status": "ok", "count": 1,
            "recordings": [{"recording_id": RID, "label": "Meeting 1",
                            "date": "2026-01-02", "has_mom": True}],
        },
        "recording_mom": {
            "status": "ok", "recording_id": RID,
            "mom": {"action_items": [
                {"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]},
        },
    })
    llm = FakeLLM([
        _tool([{"id": "c1", "name": "list_recordings", "arguments": "{}"}]),
        _tool([{"id": "c2", "name": "recording_mom",
                "arguments": f'{{"recording_id": "{RID}"}}'}]),
        _text("Trong Meeting 1, Hiếu cần viết migration."),
    ])
    graph = _build(llm, MemorySaver(), ft)
    cfg = {"configurable": {"thread_id": "rec-scope"}}

    result = await graph.ainvoke(_initial("việc của Hiếu trong Meeting 1"), cfg)

    assert not await _interrupted(graph, cfg)
    # list_recordings got meeting_id injected (it has a meeting_id property)
    assert ft.calls[0]["name"] == "list_recordings"
    assert ft.calls[0]["args"]["meeting_id"] == "bound-mid"
    # recording_mom got the chosen recording_id; meeting_id NOT injected
    assert ft.calls[1]["name"] == "recording_mom"
    assert ft.calls[1]["args"]["recording_id"] == RID
    assert "meeting_id" not in ft.calls[1]["args"]
    assert "migration" in result["final_reply"]
