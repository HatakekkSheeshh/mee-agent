"""Send endpoint threads the per-turn (UI-selected) meeting_id into the graph,
instead of grounding on the session's stored binding. None → general (no project)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api import chat as chat_api
from src.db import repositories as repo

SID = "55555555-5555-5555-5555-555555555555"
TURN_MEETING = "66666666-6666-6666-6666-666666666666"


def _fake_user():
    # ms_oid None → _graph_token_or_401 returns None (no Microsoft path).
    return SimpleNamespace(id=uuid.uuid4(), ms_oid=None)


async def test_send_passes_request_meeting_id_to_graph(monkeypatch):
    sid = uuid.UUID(SID)
    # The session's OWN binding is a DIFFERENT (legacy, ignored) meeting.
    fake_chat = SimpleNamespace(id=sid, meeting_id=uuid.uuid4(), title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(chat_api, "get_checkpointer", lambda: object())

    captured = {}

    async def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"status": "complete", "reply": "ok", "intent": None}

    monkeypatch.setattr(chat_api, "run_chat_turn", fake_run_chat_turn)

    req = chat_api.MessageSend(text="hi", meeting_id=TURN_MEETING)
    out = await chat_api.send_message(SID, req, session=object(), user=_fake_user())

    assert out["status"] == "complete"
    assert captured["meeting_id"] == TURN_MEETING  # the UI selection, not the binding


async def test_send_with_no_meeting_id_grounds_generally(monkeypatch):
    sid = uuid.UUID(SID)
    fake_chat = SimpleNamespace(id=sid, meeting_id=uuid.uuid4(), title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(chat_api, "get_checkpointer", lambda: object())

    captured = {}

    async def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"status": "complete", "reply": "ok", "intent": None}

    monkeypatch.setattr(chat_api, "run_chat_turn", fake_run_chat_turn)

    req = chat_api.MessageSend(text="hi")  # no project selected
    out = await chat_api.send_message(SID, req, session=object(), user=_fake_user())

    assert out["status"] == "complete"
    assert captured["meeting_id"] is None
