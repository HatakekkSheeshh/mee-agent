"""Option B — recording-scoped read tools.

`list_recordings` (meeting_id auto-injected) lets the agent enumerate a project's
phiên/sessions; `recording_mom` (arg recording_id) returns ONE recording's
structured MoM so the agent can answer "X's tasks in recording Y" without
mis-attributing project-wide memory.

Unit tests monkeypatch the repo helpers (no DB).
"""
from __future__ import annotations

import uuid

from src.services import tools

MID = "11111111-1111-1111-1111-111111111111"
RID = "33333333-3333-3333-3333-333333333333"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _executor(name):
    return tools.get_tool(name)["executor"]


# ─── list_recordings tool ─────────────────────────────────────────────

def test_list_recordings_is_read_tool():
    spec = tools.get_tool("list_recordings")
    assert spec is not None
    assert spec["side_effect"] is False
    # meeting_id is server-injected — it must be a schema property so the agent
    # plumbing knows to inject it (it is stripped from the LLM-facing schema).
    assert "meeting_id" in spec["schema"]["properties"]


async def test_list_recordings_returns_recordings(monkeypatch):
    rows = [
        {"recording_id": RID, "label": "Meeting 1", "date": "2026-01-02", "has_mom": True},
        {"recording_id": str(uuid.uuid4()), "label": "Buổi 2", "date": "2026-01-05", "has_mom": False},
    ]

    async def fake_list(session, mid):
        assert mid == uuid.UUID(MID)
        return rows

    monkeypatch.setattr(tools.repo, "list_recordings", fake_list)

    out = await _executor("list_recordings")(
        {"meeting_id": MID}, session=object(), user_id=UID
    )

    assert out["status"] == "ok"
    assert out["count"] == 2
    assert out["recordings"][0]["label"] == "Meeting 1"


async def test_list_recordings_requires_meeting_id():
    out = await _executor("list_recordings")({}, session=object(), user_id=UID)
    assert out.get("error")


# ─── recording_mom tool ───────────────────────────────────────────────

def test_recording_mom_is_read_tool():
    spec = tools.get_tool("recording_mom")
    assert spec is not None
    assert spec["side_effect"] is False
    assert "recording_id" in spec["schema"]["properties"]
    assert "recording_id" in spec["schema"].get("required", [])


async def test_recording_mom_returns_structured_mom(monkeypatch):
    mom = {
        "summary": "Phiên 1 chốt kiến trúc",
        "decisions": [{"topic": "DB", "description": "dùng Postgres"}],
        "action_items": [
            {"pic": "Hiếu", "deadline": "10/01/2026", "item": "viết migration"},
            {"pic": "Mai", "deadline": "Chưa xác định", "item": "POC caching"},
        ],
    }

    async def fake_mom(session, rid):
        assert rid == uuid.UUID(RID)
        return mom

    monkeypatch.setattr(tools.repo, "get_recording_mom", fake_mom)

    out = await _executor("recording_mom")(
        {"recording_id": RID}, session=object(), user_id=UID
    )

    assert out["status"] == "ok"
    assert out["recording_id"] == RID
    assert out["mom"]["action_items"][0]["pic"] == "Hiếu"


async def test_recording_mom_not_found(monkeypatch):
    async def fake_mom(session, rid):
        return None

    monkeypatch.setattr(tools.repo, "get_recording_mom", fake_mom)

    out = await _executor("recording_mom")(
        {"recording_id": RID}, session=object(), user_id=UID
    )
    assert out["status"] == "not_found"


async def test_recording_mom_requires_recording_id():
    out = await _executor("recording_mom")({}, session=object(), user_id=UID)
    assert out.get("error")
