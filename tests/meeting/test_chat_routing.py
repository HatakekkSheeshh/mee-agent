"""Task 8 — classify_intent routing.

pm-agent is now STRICTLY OPT-IN: only the '/pm-agent' prefix routes to pm_task
(deterministic, no LLM). For every other message the LLM is a grounding-only
classifier — it decides grounding required|auto and intent is ALWAYS 'agent'
(the unified agent handles meeting Q&A + Redmine via MCP). route_entry sends
pm_task → pm_call, everything else → the unified agent.
"""
from __future__ import annotations

from types import SimpleNamespace

from meeting.graphs.chat_graph import make_classify_intent, route_entry


def test_route_pm_task_goes_to_pm_call():
    assert route_entry({"intent": "pm_task"}) == "pm_call"


def test_route_agent_goes_to_agent():
    assert route_entry({"intent": "agent"}) == "agent"


def test_route_defaults_to_agent_when_missing():
    assert route_entry({}) == "agent"


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


async def test_classify_pm_agent_command_routes_to_pm_task():
    """The /pm-agent prefix opts in deterministically — no LLM call needed."""
    classify_intent = make_classify_intent(None)  # llm unused on the prefix path
    out = await classify_intent(
        {"user_message": "/pm-agent liệt kê issue overdue", "meeting_context": {}}
    )
    assert out["intent"] == "pm_task"
    # The command is stripped so pm_call forwards the real request, not the prefix.
    assert out["user_message"] == "liệt kê issue overdue"


async def test_classify_pm_agent_command_case_insensitive_and_trimmed():
    classify_intent = make_classify_intent(None)
    out = await classify_intent(
        {"user_message": "  /PM-Agent  đồng bộ issue", "meeting_context": {}}
    )
    assert out["intent"] == "pm_task"
    assert out["user_message"] == "đồng bộ issue"


async def test_classify_llm_cannot_escalate_to_pm_task():
    """Opt-in only: even if the LLM emits intent=pm_task, classify IGNORES it and
    stays on the unified agent. The /pm-agent prefix is the SOLE pm_task trigger."""
    fake = _fake_llm_returning('{"intent": "pm_task", "grounding": "auto"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent(
        {"user_message": "tạo issue trên Redmine cho việc deploy v1", "meeting_context": {}}
    )
    assert out["intent"] == "agent"


async def test_classify_returns_agent_for_meeting_question():
    fake = _fake_llm_returning('{"intent": "agent"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent(
        {"user_message": "tóm tắt cuộc họp tuần trước", "meeting_context": {}}
    )
    assert out["intent"] == "agent"


async def test_classify_intent_always_agent_off_prefix():
    """Off the /pm-agent prefix, intent is unconditionally 'agent' — any
    intent-ish field the LLM emits is ignored."""
    fake = _fake_llm_returning('{"intent": "banana", "grounding": "auto"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent({"user_message": "???", "meeting_context": {}})
    assert out["intent"] == "agent"


# ─── grounding flag (force-grounding plan, Task 1) ───────────────────

async def test_classify_emits_grounding_required():
    """Content/recording question → classify carries grounding == 'required'."""
    fake = _fake_llm_returning('{"intent": "agent", "grounding": "required"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent(
        {"user_message": "tóm tắt phiên 1", "meeting_context": {}}
    )
    assert out["intent"] == "agent"
    assert out["grounding"] == "required"


async def test_classify_grounding_auto_for_chitchat():
    fake = _fake_llm_returning('{"intent": "agent", "grounding": "auto"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent(
        {"user_message": "chào bạn", "meeting_context": {}}
    )
    assert out["grounding"] == "auto"


async def test_classify_grounding_defaults_to_auto_when_absent():
    """Model omitted grounding → parser defaults it to 'auto' (no forcing)."""
    fake = _fake_llm_returning('{"intent": "agent"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent(
        {"user_message": "gì đó", "meeting_context": {}}
    )
    assert out["grounding"] == "auto"


async def test_classify_grounding_invalid_falls_back_to_auto():
    fake = _fake_llm_returning('{"intent": "agent", "grounding": "banana"}')
    classify_intent = make_classify_intent(fake)

    out = await classify_intent({"user_message": "???", "meeting_context": {}})
    assert out["grounding"] == "auto"


async def test_classify_grounding_auto_on_error():
    """Exception path returns intent=agent + grounding=auto (no forcing on failure)."""
    fake = _fake_llm_returning("not json at all {{{")
    classify_intent = make_classify_intent(fake)

    out = await classify_intent({"user_message": "x", "meeting_context": {}})
    assert out["intent"] == "agent"
    assert out["grounding"] == "auto"


def test_classify_prompt_is_grounding_only():
    """The classify prompt asks ONLY for the grounding flag — it must NOT ask the
    model to classify intent (pm-agent is opt-in via /pm-agent, not the LLM)."""
    from meeting.graphs.chat_graph import CLASSIFY_SYSTEM_PROMPT

    assert "grounding" in CLASSIFY_SYSTEM_PROMPT
    assert "required" in CLASSIFY_SYSTEM_PROMPT
    assert "intent" not in CLASSIFY_SYSTEM_PROMPT
    assert "pm_task" not in CLASSIFY_SYSTEM_PROMPT
