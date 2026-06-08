"""Task 2 — create_task builds a structured task from MoM action_items
instead of returning a mock. Still side-effecting (HITL required).

Unit tests with monkeypatched repo.get_mom_action_items (no DB).
"""
from __future__ import annotations

import uuid

from meeting.services import tools

MID = "11111111-1111-1111-1111-111111111111"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _executor():
    return tools.get_tool("create_task")["executor"]


def test_create_task_is_side_effect():
    spec = tools.get_tool("create_task")
    assert spec is not None
    assert spec["side_effect"] is True


async def test_create_task_from_explicit_args():
    out = await _executor()(
        {"title": "Deploy v1", "assignee": "Tuấn", "deadline": "06/06/2026"},
        session=object(), user_id=UID,
    )
    assert out["status"] == "prepared"
    assert out["source"] == "explicit"
    assert out["count"] == 1
    t = out["tasks"][0]
    assert t["subject"] == "Deploy v1"
    assert t["assignee"] == "Tuấn"
    assert t["due_date"] == "06/06/2026"


async def test_create_task_from_mom_action_items(monkeypatch):
    action_items = [
        {"pic": "Tuấn", "deadline": "06/06/2026", "item": "Xử lý database migration"},
        {"pic": "Mai", "deadline": "Chưa xác định", "item": "POC caching"},
    ]

    async def fake_items(session, mid):
        assert mid == uuid.UUID(MID)
        return action_items

    monkeypatch.setattr(tools.repo, "get_mom_action_items", fake_items)

    out = await _executor()({"meeting_id": MID}, session=object(), user_id=UID)

    assert out["status"] == "prepared"
    assert out["source"] == "mom"
    assert out["count"] == 2
    subjects = [t["subject"] for t in out["tasks"]]
    assert "Xử lý database migration" in subjects
    first = out["tasks"][0]
    assert first["assignee"] == "Tuấn"
    assert first["due_date"] == "06/06/2026"


async def test_create_task_needs_title_or_meeting():
    out = await _executor()({}, session=object(), user_id=UID)
    assert out.get("error")


async def test_create_task_meeting_without_action_items(monkeypatch):
    async def fake_items(session, mid):
        return []

    monkeypatch.setattr(tools.repo, "get_mom_action_items", fake_items)

    out = await _executor()({"meeting_id": MID}, session=object(), user_id=UID)
    assert out.get("error")
