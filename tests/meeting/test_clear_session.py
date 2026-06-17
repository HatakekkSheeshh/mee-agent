"""Clear chat session (in-place) — repo + endpoint.

The shared DB is unavailable in this env (see HANDOFF.md), so — like
test_repo_recordings / test_chat_api_pm — these tests use a fake session that
records the issued statements rather than a live Postgres. They assert the
behaviour that matters: clear_chat_session deletes the session's chat_messages
AND pending_actions (scoped by session_id) and never touches the session row.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Delete

from meeting.api import chat as chat_api
from meeting.db import repositories as repo

SID = "33333333-3333-3333-3333-333333333333"


class _RecordingSession:
    """Captures executed statements; no real DB."""

    def __init__(self):
        self.executed: list = []
        self.flushed = False

    async def execute(self, stmt):
        self.executed.append(stmt)
        return None

    async def flush(self):
        self.flushed = True


async def test_clear_chat_session_deletes_messages_and_pending():
    sid = uuid.UUID(SID)
    sess = _RecordingSession()

    await repo.clear_chat_session(sess, sid)

    # Two DELETEs were issued.
    assert all(isinstance(s, Delete) for s in sess.executed)
    tables = {s.table.name for s in sess.executed}
    assert tables == {"chat_messages", "pending_actions"}
    # Each DELETE is scoped to this session_id.
    for stmt in sess.executed:
        assert sid in stmt.compile().params.values()


async def test_clear_chat_session_keeps_the_session_row():
    sid = uuid.UUID(SID)
    sess = _RecordingSession()

    await repo.clear_chat_session(sess, sid)

    tables = {s.table.name for s in sess.executed}
    assert "chat_sessions" not in tables  # the session row survives an in-place clear


# ─── endpoint: POST /sessions/{id}/clear ──────────────────────────────


async def test_clear_endpoint_clears_and_purges_checkpoint(monkeypatch):
    sid = uuid.UUID(SID)
    sess = object()
    fake_chat = SimpleNamespace(id=sid, meeting_id=uuid.uuid4(), title="Dự án Mee")

    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    clear_mock = AsyncMock()
    monkeypatch.setattr(repo, "clear_chat_session", clear_mock)
    adelete = AsyncMock()
    monkeypatch.setattr(
        chat_api, "get_checkpointer", lambda: SimpleNamespace(adelete_thread=adelete)
    )

    out = await chat_api.clear_session(SID, session=sess)

    assert out == {"status": "cleared", "session_id": SID}
    clear_mock.assert_awaited_once_with(sess, sid)
    adelete.assert_awaited_once_with(SID)  # thread_id == str(session_id)


async def test_clear_endpoint_404_when_session_missing(monkeypatch):
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=None))
    clear_mock = AsyncMock()
    monkeypatch.setattr(repo, "clear_chat_session", clear_mock)

    with pytest.raises(HTTPException) as ei:
        await chat_api.clear_session(SID, session=object())

    assert ei.value.status_code == 404
    clear_mock.assert_not_awaited()  # nothing deleted for a missing session


async def test_clear_endpoint_checkpoint_failure_is_nonfatal(monkeypatch):
    sid = uuid.UUID(SID)
    fake_chat = SimpleNamespace(id=sid, meeting_id=None, title=None)
    monkeypatch.setattr(repo, "get_chat_session", AsyncMock(return_value=fake_chat))
    clear_mock = AsyncMock()
    monkeypatch.setattr(repo, "clear_chat_session", clear_mock)
    boom = AsyncMock(side_effect=RuntimeError("no checkpointer"))
    monkeypatch.setattr(
        chat_api, "get_checkpointer", lambda: SimpleNamespace(adelete_thread=boom)
    )

    # Purge failure is logged, not raised — the DB deletion already succeeded.
    out = await chat_api.clear_session(SID, session=object())

    assert out["status"] == "cleared"
    clear_mock.assert_awaited_once()
