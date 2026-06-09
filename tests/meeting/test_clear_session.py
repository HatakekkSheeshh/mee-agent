"""Clear chat session (in-place) — repo + endpoint.

The shared DB is unavailable in this env (see HANDOFF.md), so — like
test_repo_recordings / test_chat_api_pm — these tests use a fake session that
records the issued statements rather than a live Postgres. They assert the
behaviour that matters: clear_chat_session deletes the session's chat_messages
AND pending_actions (scoped by session_id) and never touches the session row.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Delete

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
