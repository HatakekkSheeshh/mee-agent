"""load_context grounds on the per-turn meeting_id (UI selection) carried in
state, not on a session column. None → no project grounding (general turn)."""
from __future__ import annotations

import uuid

import pytest

from meeting.graphs.chat_graph import context as ctx


class _Meeting:
    def __init__(self, mid):
        self.id = mid
        self.title = "AI Innovation Project"
        self.project_summary_json = {"narrative": "Đang chạy"}
        self.recordings = []


def _patch(monkeypatch, meeting):
    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    async def fake_get_meeting(session, mid):
        # Return the meeting only when asked for the per-turn id.
        return meeting if str(mid) == str(meeting.id) else None

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)
    monkeypatch.setattr(ctx.repo, "get_meeting", fake_get_meeting)


async def test_grounds_on_state_meeting_id(monkeypatch):
    mid = uuid.uuid4()
    _patch(monkeypatch, _Meeting(mid))
    load_context = ctx.make_load_context(
        session=None, search_record=lambda pid: None, schedule_resync=lambda m: None
    )

    out = await load_context(
        {"session_id": str(uuid.uuid4()), "meeting_id": str(mid)}
    )

    assert out["meeting_context"]["id"] == str(mid)
    assert out["meeting_context"]["title"] == "AI Innovation Project"
    assert out["resolved_meeting_id"] == str(mid)


async def test_no_project_grounding_when_state_meeting_id_none(monkeypatch):
    _patch(monkeypatch, _Meeting(uuid.uuid4()))
    load_context = ctx.make_load_context(
        session=None, search_record=lambda pid: None, schedule_resync=lambda m: None
    )

    out = await load_context({"session_id": str(uuid.uuid4())})  # no meeting_id

    assert out["meeting_context"] == {}
    assert out["project_memory"] == ""
    assert out["resolved_meeting_id"] is None
