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


def test_prompt_steers_redmine_to_company_login_not_display_name():
    p = _agent_system_prompt({
        "user_name": "Hieu Nguyen Quoc",
        "user_email": "hieunq3@vng.com.vn",
    })
    # The Redmine identity (email local-part) is surfaced...
    assert "hieunq3" in p
    # ...with an explicit instruction to use it for Redmine, not the display name.
    assert "Redmine" in p
    assert "KHÔNG dùng tên hiển thị" in p
    assert "Hieu Nguyen Quoc" in p


def test_prompt_no_redmine_identity_line_without_email():
    p = _agent_system_prompt({"user_name": "An Nguyễn"})
    assert "Người dùng hiện tại" in p
    assert "Định danh trên Redmine" not in p  # no email → no Redmine-login steering


async def test_load_context_loads_user_name_and_role(monkeypatch):
    uid = uuid.uuid4()
    user = SimpleNamespace(
        display_name="An Nguyễn", email="annd2@vng.com.vn",
        role=SimpleNamespace(name="Project Manager"),
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
    assert out["user_email"] == "annd2@vng.com.vn"


async def test_load_context_loads_user_meetings_roster(monkeypatch):
    uid = uuid.uuid4()
    user = SimpleNamespace(display_name="An", email="a@b.com", role=None)

    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    async def fake_roster(session, user_id):
        return [
            SimpleNamespace(id=uuid.uuid4(), title="GIP"),
            SimpleNamespace(id=uuid.uuid4(), title="AI Innovation Projects"),
        ]

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)
    monkeypatch.setattr(ctx.repo, "list_meetings_for_user", fake_roster)

    class _Sess:
        async def get(self, model, key):
            return user

    load_context = ctx.make_load_context(_Sess())
    out = await load_context(
        {"session_id": str(uuid.uuid4()), "user_id": str(uid)}
    )

    titles = {m["title"] for m in out["user_meetings"]}
    assert titles == {"GIP", "AI Innovation Projects"}


# ── A2. project roster so the agent recognises OTHER projects ────────────

def test_prompt_lists_user_projects_for_switch_recognition():
    # The bug: bound to "AI Innovation Projects", asked about "GIP", the model
    # had no roster so it assumed GIP == the current meeting and never switched.
    p = _agent_system_prompt({
        "meeting_context": {"title": "AI Innovation Projects"},
        "user_meetings": [
            {"id": "1", "title": "GIP"},
            {"id": "2", "title": "AI Innovation Projects"},
            {"id": "3", "title": "Project 2"},
        ],
    })
    assert "Các cuộc họp của bạn" in p   # a roster block exists (meeting terms)
    assert "GIP" in p                    # the other meeting is visible by name
    # ...and an explicit anti-conflation rule: a named meeting is NOT an
    # abbreviation of the current meeting — switch instead.
    assert "viết tắt" in p


def test_prompt_omits_meeting_roster_when_no_user_meetings():
    p = _agent_system_prompt({"meeting_context": {"title": "X"}})
    # The roster LISTING (distinguished by its parenthetical) is gone; the
    # switch_meeting rule may still reference the roster heading by name.
    assert "để nhận diện cuộc họp" not in p


def test_prompt_splits_meeting_from_redmine_project():
    # meeting/cuộc họp = Mee container; project = Redmine. The prompt must say so
    # and name the Redmine tool so the model stops using it for meeting questions.
    p = _agent_system_prompt({"user_meetings": [{"id": "1", "title": "GIP"}]})
    assert "list_meetings" in p
    assert "get_redmine_projects" in p
    assert "Redmine" in p


def test_prompt_roster_dedupes_titles():
    p = _agent_system_prompt({
        "user_meetings": [
            {"id": "1", "title": "Test"},
            {"id": "2", "title": "Test"},
            {"id": "3", "title": "Test"},
        ],
    })
    # Three rows, one title — listed once, not three times.
    assert p.count("Test") == 1


# ── B. don't auto-fix editable-field tool errors ─────────────────────────

def test_prompt_tells_agent_not_to_autofix_editable_tool_errors():
    p = _agent_system_prompt({})
    assert "create_task" in p
    assert "create_redmine_issue" in p
    assert "KHÔNG tự ý sửa" in p  # forbids guessing corrected args + retrying
