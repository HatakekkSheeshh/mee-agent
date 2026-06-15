"""Rename a chat session — repo + endpoint.

Offline (no live DB): fakes stand in for the AsyncSession. Renaming just sets
chat_sessions.title; 404 when the session is missing.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from meeting.api import chat as chat_api
from meeting.db import repositories as repo

SID = "77777777-7777-7777-7777-777777777777"


class _FakeSession:
    def __init__(self, chat):
        self._chat = chat
        self.flushed = False

    async def get(self, model, key):
        return self._chat

    async def flush(self):
        self.flushed = True


async def test_rename_chat_session_sets_title():
    sid = uuid.UUID(SID)
    chat = SimpleNamespace(id=sid, title=None)
    sess = _FakeSession(chat)

    out = await repo.rename_chat_session(sess, sid, "Sprint planning")

    assert out is chat
    assert chat.title == "Sprint planning"
    assert sess.flushed is True


async def test_rename_chat_session_missing_returns_none():
    sess = _FakeSession(None)
    out = await repo.rename_chat_session(sess, uuid.UUID(SID), "whatever")
    assert out is None


async def test_rename_endpoint_updates_title(monkeypatch):
    sid = uuid.UUID(SID)
    chat = SimpleNamespace(id=sid, title="Sprint planning")
    monkeypatch.setattr(repo, "rename_chat_session", AsyncMock(return_value=chat))

    out = await chat_api.rename_session(
        SID, chat_api.SessionRename(title="Sprint planning"), session=object()
    )

    assert out == {"id": SID, "title": "Sprint planning"}


async def test_rename_endpoint_404_when_missing(monkeypatch):
    monkeypatch.setattr(repo, "rename_chat_session", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as ei:
        await chat_api.rename_session(
            SID, chat_api.SessionRename(title="x"), session=object()
        )

    assert ei.value.status_code == 404
