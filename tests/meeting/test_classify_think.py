"""classify_intent must survive a model that leaks <think> reasoning into the
content (minimax ignores enable_thinking sometimes): strip it before parsing,
and never blow up with a JSONDecodeError traceback on unparseable output.
"""
from __future__ import annotations

from types import SimpleNamespace

from meeting.graphs.chat_graph.classify import make_classify_intent


def _llm(content):
    def create(**kw):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


async def test_classify_strips_think_then_parses_grounding():
    llm = _llm('<think>user hỏi về phiên họp, cần đọc dữ liệu</think>\n{"grounding": "required"}')
    out = await make_classify_intent(llm=llm)({"user_message": "Meeting 1 có gì?"})
    assert out["intent"] == "agent"
    assert out["grounding"] == "required"
    assert "error" not in out


async def test_classify_unparseable_falls_back_quietly():
    # only leaked reasoning, truncated before any JSON → recover, no exception path
    llm = _llm("<think>đang suy nghĩ thì bị cắt")
    out = await make_classify_intent(llm=llm)({"user_message": "chào bạn"})
    assert out["intent"] == "agent"
    assert out["grounding"] == "auto"
    assert "error" not in out          # recovered, not an unexpected crash
