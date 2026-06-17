"""Hard-delete a chat session — repo + endpoint.

Offline (no live DB): a recording fake session captures issued statements, like
test_clear_session. Delete differs from clear by ALSO removing the session row.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Delete

from src.api import chat as chat_api
from src.db import repositories as repo

SID = "44444444-4444-4444-4444-444444444444"


class _RecordingSession:
    def __init__(self):
        self.executed: list = []
        self.flushed = False

    async def execute(self, stmt):
        self.executed.append(stmt)
        return None

    async def flush(self):
        self.flushed = True


async def test_delete_chat_session_deletes_messages_pending_and_row():
    sid = uuid.UUID(SID)
    sess = _RecordingSession()

    await repo.delete_chat_session(sess, sid)

    assert all(isinstance(s, Delete) for s in sess.executed)
    tables = {s.table.name for s in sess.executed}
    assert tables == {"chat_messages", "pending_actions", "chat_sessions"}
    # Every DELETE is scoped to this session id.
    for stmt in sess.executed:
        assert sid in stmt.compile().params.values()


# ─── endpoint: DELETE /sessions/{id} ──────────────────────────────────


async def test_delete_endpoint_hard_deletes_and_purges_checkpoint(monkeypatch):
    sid = uuid.UUID(SID)
    sess = object()
    fake_chat = SimpleNamespace(id=sid, meeting_id=None, title="Dự án Mee")

    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    delete_mock = AsyncMock()
    monkeypatch.setattr(repo, "delete_chat_session", delete_mock)
    adelete = AsyncMock()
    monkeypatch.setattr(
        chat_api, "get_checkpointer", lambda: SimpleNamespace(adelete_thread=adelete)
    )

    out = await chat_api.delete_session(SID, session=sess)

    assert out == {"status": "deleted", "session_id": SID}
    delete_mock.assert_awaited_once_with(sess, sid)
    adelete.assert_awaited_once_with(SID)  # thread_id == str(session_id)


async def test_delete_endpoint_404_when_session_missing(monkeypatch):
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=None))
    delete_mock = AsyncMock()
    monkeypatch.setattr(repo, "delete_chat_session", delete_mock)

    with pytest.raises(HTTPException) as ei:
        await chat_api.delete_session(SID, session=object())

    assert ei.value.status_code == 404
    delete_mock.assert_not_awaited()


async def test_delete_endpoint_checkpoint_failure_is_nonfatal(monkeypatch):
    sid = uuid.UUID(SID)
    fake_chat = SimpleNamespace(id=sid, meeting_id=None, title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    monkeypatch.setattr(repo, "delete_chat_session", AsyncMock())
    boom = AsyncMock(side_effect=RuntimeError("no checkpointer"))
    monkeypatch.setattr(
        chat_api, "get_checkpointer", lambda: SimpleNamespace(adelete_thread=boom)
    )

    out = await chat_api.delete_session(SID, session=object())

    assert out["status"] == "deleted"  # purge failure logged, not raised
