"""update_to_events — pure mapping from astream 'updates' chunks to UI step events.

No DB / no graph: the mapper is a pure function (repo convention: DB-touching
paths are exercised via fakes, pure helpers directly).
"""
from meeting.graphs.chat_graph import update_to_events


def test_load_context_emits_context_step():
    assert update_to_events("load_context", {"recent_messages": []}) == [
        {"type": "step", "step": "context"}
    ]


def test_classify_includes_intent_when_present():
    assert update_to_events("classify_intent", {"intent": "agent"}) == [
        {"type": "step", "step": "classify", "intent": "agent"}
    ]


def test_classify_without_intent_still_emits():
    assert update_to_events("classify_intent", {}) == [
        {"type": "step", "step": "classify"}
    ]


def test_agent_tool_round_emits_tool_names():
    delta = {
        "agent_route": "tools",
        "agent_messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "1", "function": {"name": "list_recordings", "arguments": "{}"}},
                    {"id": "2", "function": {"name": "recording_mom", "arguments": "{}"}},
                ],
            },
        ],
    }
    assert update_to_events("agent", delta) == [
        {"type": "step", "step": "tool_call", "tools": ["list_recordings", "recording_mom"]}
    ]


def test_agent_final_answer_emits_nothing():
    # The final reply surfaces via the terminal result event, not a step.
    assert update_to_events("agent", {"agent_route": "finish", "final_reply": "xin chào"}) == []


def test_agent_tools_emits_tool_done():
    assert update_to_events("agent_tools", {"agent_messages": []}) == [
        {"type": "step", "step": "tool_done"}
    ]


def test_pm_nodes_emit_pm_step():
    assert update_to_events("pm_call", {}) == [{"type": "step", "step": "pm"}]
    assert update_to_events("pm_await", {}) == [{"type": "step", "step": "pm"}]


def test_unknown_nodes_emit_nothing():
    # New graph nodes must degrade silently, not break the stream.
    assert update_to_events("save_reply", {"final_reply": "x"}) == []
    assert update_to_events("agent_approve", {}) == []
    assert update_to_events("__some_future_node__", {}) == []


def test_malformed_tool_calls_do_not_crash():
    delta = {
        "agent_route": "tools",
        "agent_messages": [{"role": "assistant", "tool_calls": [{"id": "1"}]}],
    }
    assert update_to_events("agent", delta) == [
        {"type": "step", "step": "tool_call", "tools": ["?"]}
    ]
