"""Agent system prompt: (A) user identity/role injection so the agent knows who
'tôi/của tôi' is for tool calls, and (B) a rule that editable-field tool errors
(create_task / create_redmine_issue) are the user's to fix on the card — the
agent must surface the error, not silently retry with guessed arguments."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from meeting.graphs._chat_prompts import _agent_system_prompt
from meeting.graphs.chat_graph import context as ctx


# ── A. user-context injection ────────────────────────────────────────────

def test_prompt_includes_user_name_and_role_when_present():
    p = _agent_system_prompt({"user_name": "An Nguyễn", "user_role": "Project Manager"})
    assert "Người dùng hiện tại" in p
    assert "An Nguyễn" in p
    assert "Project Manager" in p
    assert "tôi" in p.lower()  # maps "tôi/của tôi" to this user


def test_prompt_omits_user_block_when_absent():
    p = _agent_system_prompt({})
    assert "Người dùng hiện tại" not in p


async def test_load_context_loads_user_name_and_role(monkeypatch):
    uid = uuid.uuid4()
    user = SimpleNamespace(
        display_name="An Nguyễn", role=SimpleNamespace(name="Project Manager")
    )

    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)

    class _Sess:
        async def get(self, model, key):
            return user

    load_context = ctx.make_load_context(_Sess())
    out = await load_context(
        {"session_id": str(uuid.uuid4()), "user_id": str(uid)}
    )

    assert out["user_name"] == "An Nguyễn"
    assert out["user_role"] == "Project Manager"


# ── B. don't auto-fix editable-field tool errors ─────────────────────────

def test_prompt_tells_agent_not_to_autofix_editable_tool_errors():
    p = _agent_system_prompt({})
    assert "create_task" in p
    assert "create_redmine_issue" in p
    assert "KHÔNG tự ý sửa" in p  # forbids guessing corrected args + retrying
