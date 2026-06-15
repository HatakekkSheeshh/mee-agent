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

from meeting.api import chat as chat_api
from meeting.db import repositories as repo

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
