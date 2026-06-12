"""Recover tool calls minimax-m2.5 leaks into text content as XML.

The serving layer (VNG MaaS) doesn't always parse minimax's native tool-call
format into OpenAI tool_calls, so they arrive in message.content like:
    minimax:tool_call
    <invoke name="send_email"><parameter name="to">anhvd6</parameter>...</invoke>
parse_leaked_tool_calls recovers them; make_agent falls back to it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from meeting.graphs import _chat_serde as serde
from meeting.graphs.chat_graph import make_agent

LEAKED = (
    "minimax:tool_call\n"
    '<invoke name="send_email">\n'
    '<parameter name="to">anhvd6</parameter>\n'
    '<parameter name="subject">Hối deadline</parameter>\n'
    '<parameter name="body">Xin chào anh, nhắc deadline.</parameter>\n'
    "</invoke>"
)


def test_parses_leaked_send_email():
    calls, text = serde.parse_leaked_tool_calls(LEAKED)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "send_email"
    assert calls[0]["type"] == "function"
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"to": "anhvd6", "subject": "Hối deadline", "body": "Xin chào anh, nhắc deadline."}
    assert text == ""  # pure tool call, no prose


def test_no_markup_returns_text_unchanged():
    calls, text = serde.parse_leaked_tool_calls("Chào bạn, mình là Mee.")
    assert calls == []
    assert text == "Chào bạn, mình là Mee."


def test_handles_minimax_wrapper_and_leading_prose():
    content = (
        "Mình sẽ gửi nhé."
        '<minimax:tool_call><invoke name="send_email">'
        '<parameter name="to">x</parameter></invoke></minimax:tool_call>'
    )
    calls, text = serde.parse_leaked_tool_calls(content)
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"to": "x"}
    assert text == "Mình sẽ gửi nhé."


def test_multiple_invokes():
    content = (
        '<invoke name="a"><parameter name="x">1</parameter></invoke>'
        '<invoke name="b"><parameter name="y">2</parameter></invoke>'
    )
    calls, _ = serde.parse_leaked_tool_calls(content)
    assert [c["function"]["name"] for c in calls] == ["a", "b"]
    assert json.loads(calls[1]["function"]["arguments"]) == {"y": "2"}


def test_missing_closing_invoke_tag_still_parses():
    content = '<invoke name="create_task"><parameter name="title">Deploy</parameter>'
    calls, _ = serde.parse_leaked_tool_calls(content)
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"title": "Deploy"}


def test_none_content():
    assert serde.parse_leaked_tool_calls(None) == ([], "")


# ── behavioral: the agent node recovers a leaked tool call ──

def _resp_text_content(content):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


class _FakeLLM:
    def __init__(self, scripted):
        self._s = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        i = len(self.calls)
        self.calls.append(kw)
        return self._s[i]


class _Toolset:
    def list_tools(self):
        return [{"name": "send_email", "description": "", "side_effect": True,
                 "schema": {"type": "object", "properties": {}}}]

    def get_tool(self, n):
        return None


async def test_agent_recovers_leaked_tool_call():
    llm = _FakeLLM([_resp_text_content(LEAKED)])
    agent = make_agent(llm, tools=_Toolset())
    out = await agent({
        "agent_messages": [{"role": "user", "content": "email anhvd6 hối deadline"}],
        "agent_rounds": 0,
    })
    assert out["agent_route"] == "tools"
    last = out["agent_messages"][-1]
    assert last["role"] == "assistant"
    assert last["tool_calls"][0]["function"]["name"] == "send_email"
    assert json.loads(last["tool_calls"][0]["function"]["arguments"])["to"] == "anhvd6"


async def test_agent_plain_text_still_finishes():
    llm = _FakeLLM([_resp_text_content("Chào bạn, mình là Mee.")])
    agent = make_agent(llm, tools=_Toolset())
    out = await agent({
        "agent_messages": [{"role": "user", "content": "bạn là ai"}],
        "agent_rounds": 0,
    })
    assert out["agent_route"] == "finish"
    assert out["final_reply"] == "Chào bạn, mình là Mee."
