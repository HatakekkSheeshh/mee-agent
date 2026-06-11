"""Live finding 2026-06-11: gemma attaches a premature done-claim to its
tool_call ("Đã gửi email nhắc deadline cho bạn An (annd2) rồi nhé!"), and the
FE showed it as a chat bubble NEXT TO the approval card — before anything ran.

Harness guard: agent_approve must NOT surface content that rode along with a
tool_call as the card's `rationale`. (Prompt hardening also added, but this is
the deterministic layer.)
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver

from tests.meeting.test_agent_loop import _build, _initial, _install, _interrupt_value

NARRATION = "Đã gửi email nhắc deadline cho bạn An (annd2) rồi nhé!"


class _NarratingLLM:
    """Always returns a send_email tool_call WITH attached narration content."""

    def __init__(self):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        tc = SimpleNamespace(
            id="t1",
            type="function",
            function=SimpleNamespace(
                name="send_email",
                arguments=json.dumps({"to": "annd2", "subject": "nhắc deadline"}),
            ),
        )
        msg = SimpleNamespace(content=NARRATION, tool_calls=[tc])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


async def test_tool_call_narration_is_not_surfaced_as_rationale():
    graph = _build(_NarratingLLM(), MemorySaver(), _install())
    config = {"configurable": {"thread_id": "narration"}}
    await graph.ainvoke(_initial("email nhắc deadline cho annd2"), config=config)

    pending = await _interrupt_value(graph, config)
    assert pending is not None and pending["tool"] == "send_email"
    # The premature "đã gửi" claim must NOT reach the card / chat bubble.
    assert pending["rationale"] == ""
    assert NARRATION not in json.dumps(pending, ensure_ascii=False)
