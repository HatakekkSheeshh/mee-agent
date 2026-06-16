"""When the agent loop hits MAX_AGENT_ROUNDS, it must SYNTHESIZE a final answer
from the tool results already gathered (one tool-less LLM call) — not echo a
stale/unrelated earlier assistant message (the GOAT/Ronaldo bug)."""
from __future__ import annotations

from types import SimpleNamespace

from meeting.graphs.chat_graph.agent import make_agent
from meeting.graphs._chat_state import MAX_AGENT_ROUNDS


def _llm(content, capture):
    def create(**kw):
        capture.append(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _capped_state():
    return {
        "agent_rounds": MAX_AGENT_ROUNDS,
        "session_id": "s",
        "user_message": "Meeting 1 đi",
        "meeting_context": {"title": "Dự án X"},
        "agent_messages": [
            {"role": "user", "content": "Meeting 1 đi"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "t", "function": {"name": "recording_mom", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t", "content": '{"decisions": ["A", "B"]}'},
        ],
    }


async def test_max_rounds_synthesizes_final_answer_tool_less():
    cap: list[dict] = []
    out = await make_agent(_llm("Biên bản Meeting 1: chốt A, B.", cap))(_capped_state())

    assert out["agent_route"] == "finish"
    assert out["final_reply"] == "Biên bản Meeting 1: chốt A, B."
    # the recovery call must be tool-less so the model can't keep thrashing
    assert cap and cap[-1]["tool_choice"] == "none"


async def test_max_rounds_falls_back_when_synthesis_empty():
    def boom_create(**kw):
        raise RuntimeError("model down")
    llm = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom_create)))

    out = await make_agent(llm)(_capped_state())
    assert out["agent_route"] == "finish"
    assert out["final_reply"]  # non-empty graceful fallback, never blank
