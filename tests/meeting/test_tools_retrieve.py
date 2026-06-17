"""Task 1 — the `retrieve` read tool (auto-retrieval over a meeting).

Unit tests with a fake memory_service + monkeypatched repo.get_meeting (no DB):
  - retrieve returns ranked chunks from memory_service hits,
  - it forwards meeting_id / user_id / query scoping,
  - empty hits fall back to the meeting's MoM text,
  - empty everything yields an empty (not errored) result,
  - it is a read tool (no HITL),
  - missing meeting_id errors cleanly.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.services import tools
from src.services.memory_service import MemoryEvent

MID = "11111111-1111-1111-1111-111111111111"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _executor():
    return tools.get_tool("retrieve")["executor"]


class FakeMemoryService:
    def __init__(self, events):
        self._events = events
        self.calls: list[dict] = []

    async def retrieve(
        self, query="", top_k=5, *, db_session=None, user_id=None,
        meeting_id=None, **kw,
    ):
        self.calls.append(
            {"query": query, "top_k": top_k, "user_id": user_id, "meeting_id": meeting_id}
        )
        return list(self._events)


def _fake_meeting(mom_json):
    rec = SimpleNamespace(
        id=uuid.uuid4(), title="Phiên 1", session_label="s1",
        purpose=None, mom_json=mom_json,
    )
    return SimpleNamespace(id=uuid.UUID(MID), title="Dự án Mee", recordings=[rec])


# ─── tests ───────────────────────────────────────────────────────────

def test_retrieve_is_read_tool():
    spec = tools.get_tool("retrieve")
    assert spec is not None
    assert spec["side_effect"] is False


async def test_retrieve_returns_ranked_chunks(monkeypatch):
    events = [
        MemoryEvent(meeting_id=MID, topic="deploy",
                    text="Quyết định deploy v1 vào thứ 6", event_type="decision"),
        MemoryEvent(meeting_id=MID, topic="db", text="Tuấn xử lý migration",
                    event_type="action_item", speaker="Tuấn"),
    ]
    svc = FakeMemoryService(events)
    monkeypatch.setattr(tools, "get_memory_service", lambda: svc)

    out = await _executor()(
        {"meeting_id": MID, "query": "deploy v1"}, session=object(), user_id=UID
    )

    assert out["status"] == "ok"
    assert out["source"] == "memory"
    assert out["count"] == 2
    texts = [c["text"] for c in out["chunks"]]
    assert "Quyết định deploy v1 vào thứ 6" in texts
    # forwarded scoping
    assert svc.calls[0]["meeting_id"] == uuid.UUID(MID)
    assert svc.calls[0]["user_id"] == UID
    assert svc.calls[0]["query"] == "deploy v1"


async def test_retrieve_empty_falls_back_to_mom(monkeypatch):
    svc = FakeMemoryService([])  # no embeddings / no events
    monkeypatch.setattr(tools, "get_memory_service", lambda: svc)
    mom = {
        "summary": "Họp tuần 1",
        "decisions": [{"topic": "deploy", "description": "deploy v1 thứ 6"}],
        "action_items": [{"pic": "Tuấn", "deadline": "06/06/2026", "item": "migration"}],
    }

    async def fake_get_meeting(session, mid):
        return _fake_meeting(mom)

    monkeypatch.setattr(tools.repo, "get_meeting", fake_get_meeting)

    out = await _executor()(
        {"meeting_id": MID, "query": "deploy"}, session=object(), user_id=UID
    )

    assert out["status"] == "ok"
    assert out["source"] == "mom"
    assert out["count"] >= 1
    joined = " ".join(c["text"] for c in out["chunks"])
    assert "deploy v1" in joined


async def test_retrieve_empty_everything(monkeypatch):
    svc = FakeMemoryService([])
    monkeypatch.setattr(tools, "get_memory_service", lambda: svc)

    async def fake_get_meeting(session, mid):
        return _fake_meeting(None)  # recording without a MoM

    monkeypatch.setattr(tools.repo, "get_meeting", fake_get_meeting)

    out = await _executor()(
        {"meeting_id": MID, "query": "x"}, session=object(), user_id=UID
    )
    assert out["status"] == "ok"
    assert out["source"] == "empty"
    assert out["chunks"] == []


async def test_retrieve_requires_meeting_id():
    out = await _executor()({"query": "x"}, session=object(), user_id=UID)
    assert out.get("error")
