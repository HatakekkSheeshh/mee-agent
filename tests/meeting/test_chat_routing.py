"""
Task 3 — classify_intent gains a `pm_task` intent and route_after_classify
sends it to the `pm_call` node. Pure-function routing tests + one classify
test with a monkeypatched LLM (no network).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from meeting.graphs import chat_graph
from meeting.graphs.chat_graph import classify_intent, route_after_classify


def test_route_pm_task_goes_to_pm_call():
    assert route_after_classify({"intent": "pm_task", "proposed_tool": None}) == "pm_call"


def test_route_question_unchanged():
    assert route_after_classify({"intent": "question"}) == "answer"


def test_route_tool_unchanged():
    # A tool intent with a named tool still routes to propose_action (HITL).
    state = {"intent": "tool", "proposed_tool": "send_email"}
    assert route_after_classify(state) == "propose_action"


def _fake_llm_returning(json_text: str):
    """Build a fake OpenAI-style client whose completion returns json_text."""
    message = SimpleNamespace(content=json_text)
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice])

    class _Completions:
        def create(self, **kwargs):
            return response

    class _Chat:
        completions = _Completions()

    return SimpleNamespace(chat=_Chat())


async def test_classify_returns_pm_task(monkeypatch):
    fake = _fake_llm_returning(
        '{"intent": "pm_task", "proposed_tool": null, "proposed_args": null,'
        ' "rationale": "user muốn tạo issue Redmine"}'
    )
    monkeypatch.setattr(chat_graph, "_llm_client", lambda: fake)

    out = await classify_intent(
        {"user_message": "tạo issue cho việc deploy v1", "meeting_context": {}}
    )
    assert out["intent"] == "pm_task"
