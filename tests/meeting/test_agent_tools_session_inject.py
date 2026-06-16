"""agent_tools injects the chat session_id into fact tools (remember_fact /
forget_fact) so the stored marker can audit which session asserted the fact.
The id is plumbed server-side from state — it's not in the tool schema, so the
LLM never supplies it."""
from __future__ import annotations

import json
import uuid

from meeting.graphs.chat_graph.agent import make_agent_tools

UID = uuid.uuid4()


class _FakeTS:
    def __init__(self):
        self.captured: dict = {}

    def get_tool(self, name):
        return {"name": name, "side_effect": False,
                "schema": {"type": "object", "properties": {"text": {"type": "string"}}}}

    async def execute_tool(self, name, args, *, session, user_id):
        self.captured = {"name": name, "args": args}
        return {"status": "remembered"}


def _state(tool_name, session_id="sess-xyz"):
    return {
        "session_id": session_id,
        "user_id": str(UID),
        "resolved_meeting_id": None,
        "agent_messages": [{
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "c1",
                "function": {"name": tool_name,
                             "arguments": json.dumps({"text": "Gọi tôi là Ronaldo", "scope": "user"})},
            }],
        }],
    }


async def test_session_id_injected_for_remember_fact():
    ts = _FakeTS()
    await make_agent_tools(object(), tools=ts)(_state("remember_fact"))
    assert ts.captured["args"]["session_id"] == "sess-xyz"


async def test_session_id_not_injected_for_other_tools():
    ts = _FakeTS()
    await make_agent_tools(object(), tools=ts)(_state("list_recordings"))
    assert "session_id" not in ts.captured["args"]
