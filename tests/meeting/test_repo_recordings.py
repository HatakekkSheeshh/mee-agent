"""Option B — recording-scoped repo helpers.

`list_recordings` maps a meeting's recordings → lightweight dicts the agent uses
to resolve "Meeting 1"/ordinal/date → recording_id; `get_recording_mom` fetches
one recording's stored MoM.

Unit tests with a monkeypatched repo.get_meeting / a fake session (no DB).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from types import SimpleNamespace

from src.db import repositories as repo

MID = "11111111-1111-1111-1111-111111111111"


# ─── list_recordings ──────────────────────────────────────────────────

async def test_list_recordings_maps_label_date_has_mom(monkeypatch):
    rec_first = SimpleNamespace(
        id=uuid.uuid4(), title="Phiên khai mạc", session_label="s1",
        date=date(2026, 1, 2), started_at=datetime(2026, 1, 2, 9, 0),
        mom_json={"summary": "có biên bản"},
    )
    rec_second = SimpleNamespace(
        id=uuid.uuid4(), title=None, session_label="Buổi 2",
        date=None, started_at=datetime(2026, 1, 5, 9, 0),
        mom_json=None,
    )
    # recordings come back in arbitrary order; list_recordings sorts chronologically.
    meeting = SimpleNamespace(id=uuid.UUID(MID), recordings=[rec_second, rec_first])

    async def fake_get_meeting(session, mid):
        assert mid == uuid.UUID(MID)
        return meeting

    monkeypatch.setattr(repo, "get_meeting", fake_get_meeting)

    out = await repo.list_recordings(object(), uuid.UUID(MID))

    assert [r["label"] for r in out] == ["Phiên khai mạc", "Buổi 2"]
    assert out[0]["recording_id"] == str(rec_first.id)
    assert out[0]["has_mom"] is True
    assert out[1]["has_mom"] is False
    # explicit event date wins; missing date falls back to started_at's date
    assert out[0]["date"] == "2026-01-02"
    assert out[1]["date"] == "2026-01-05"


def test_recording_sort_key_breaks_started_at_ties_by_id_deterministically():
    # Equal started_at must NOT reorder between runs: id (random UUID) is a stable
    # tiebreak only, never an order signal. Same input → same order regardless of
    # the list's incoming arrangement.
    ts = datetime(2026, 1, 2, 9, 0)
    a = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000001"), started_at=ts)
    b = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000002"), started_at=ts)
    assert sorted([b, a], key=repo.recording_sort_key) == [a, b]
    assert sorted([a, b], key=repo.recording_sort_key) == [a, b]


async def test_list_recordings_no_meeting_returns_empty(monkeypatch):
    async def fake_get_meeting(session, mid):
        return None

    monkeypatch.setattr(repo, "get_meeting", fake_get_meeting)

    out = await repo.list_recordings(object(), uuid.UUID(MID))
    assert out == []


# ─── get_recording_mom ────────────────────────────────────────────────

class _FakeSession:
    def __init__(self, obj):
        self._obj = obj

    async def get(self, model, pk):
        return self._obj


async def test_get_recording_mom_returns_stored_mom():
    rec = SimpleNamespace(mom_json={"summary": "biên bản phiên 1"})
    out = await repo.get_recording_mom(_FakeSession(rec), uuid.uuid4())
    assert out == {"summary": "biên bản phiên 1"}


async def test_get_recording_mom_missing_recording_returns_none():
    out = await repo.get_recording_mom(_FakeSession(None), uuid.uuid4())
    assert out is None
