"""Regression — per-turn loop state must reset across messages on one thread.

thread_id = session_id is reused for a whole chat session, and LangGraph
checkpoints the full ChatState on it. A new user message must therefore start
with the loop buffers/counters cleared, or:
  - agent_messages persists → _seed_agent_messages is skipped → the NEW user
    message is silently dropped (agent re-answers the old context);
  - pm_rounds accumulates → a fresh pm message instantly hits PM_MAX_ROUNDS.

These tests cover the pure reset helper + the cross-turn behavior on a minimal
agent graph (fake LLM + MemorySaver, no DB).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from meeting.graphs.chat_graph import (
    ChatState,
    _initial_turn_state,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_tools,
)


def _resp(text):
    msg = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._c))

    def _c(self, **kw):
        self.calls.append(kw)
        return _resp(f"answer #{len(self.calls)}")

    def user_msgs(self, call_idx):
        return [m["content"] for m in self.calls[call_idx]["messages"] if m["role"] == "user"]


def _build(llm):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm))
    g.add_node("agent_tools", make_agent_tools(object()))
    g.add_node("agent_approve", make_agent_approve())
    g.add_node("agent_execute", make_agent_execute(object()))
    g.add_node("save_reply", lambda s: {})
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route_after_agent,
                            {"agent_tools": "agent_tools", "save_reply": "save_reply"})
    g.add_conditional_edges("agent_tools", route_after_agent_tools,
                            {"agent": "agent", "agent_approve": "agent_approve"})
    g.add_edge("agent_approve", "agent_execute")
    g.add_edge("agent_execute", "agent")
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=MemorySaver())


# ─── pure helper ──────────────────────────────────────────────────────

def test_initial_turn_state_resets_loop_fields():
    st = _initial_turn_state(
        session_id="s", user_id="u", user_message="hỏi", meeting_id="m",
    )
    assert st["session_id"] == "s"
    assert st["user_message"] == "hỏi"
    assert st["meeting_id"] == "m"
    # loop state cleared
    assert st["agent_messages"] == []
    assert st["agent_rounds"] == 0
    assert st["pending_tool"] is None
    assert st["pm_rounds"] == 0
    assert st["pm_task_id"] is None
    assert st["pm_context_id"] is None
    assert st["pm_next_payload"] is None


# ─── cross-turn behavior ──────────────────────────────────────────────

async def test_new_turn_reseeds_message(monkeypatch):
    """With _initial_turn_state, a second message on the same thread is seen."""
    llm = FakeLLM()
    graph = _build(llm)
    cfg = {"configurable": {"thread_id": "same-session"}}
    uid = str(uuid.uuid4())

    await graph.ainvoke(_initial_turn_state("same-session", uid, "câu hỏi 1", None), cfg)
    await graph.ainvoke(_initial_turn_state("same-session", uid, "câu hỏi 2", None), cfg)

    assert llm.user_msgs(0) == ["câu hỏi 1"]
    # The fix: turn 2's message reaches the model (not dropped).
    assert "câu hỏi 2" in llm.user_msgs(1)
    assert "câu hỏi 1" not in llm.user_msgs(1)


async def test_without_reset_message_is_dropped(monkeypatch):
    """Control: invoking the same thread WITHOUT reset reproduces the bug."""
    llm = FakeLLM()
    graph = _build(llm)
    cfg = {"configurable": {"thread_id": "buggy"}}
    uid = str(uuid.uuid4())

    await graph.ainvoke(
        {"user_id": uid, "resolved_meeting_id": None, "user_message": "first"}, cfg)
    await graph.ainvoke(
        {"user_id": uid, "resolved_meeting_id": None, "user_message": "second"}, cfg)

    # Bug: turn 2's "second" never reaches the model — only "first" persists.
    assert llm.user_msgs(1) == ["first"]
