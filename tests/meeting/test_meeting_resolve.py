"""Task 3 — resolve which meeting the user means.

Default = the chat's bound meeting_id. If a title is named, ILIKE-resolve via
repo.find_meetings_by_title (most-recent first); on ambiguity pick the most
recent; on no match fall back to bound.

Unit tests monkeypatch repo.find_meetings_by_title (no DB).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from meeting.graphs import chat_graph

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _meeting(title: str):
    return SimpleNamespace(id=uuid.uuid4(), title=title)


async def test_resolve_bound_default_when_no_title():
    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title=None
    )
    assert out["meeting_id"] == "bound-id"
    assert out["resolved_by"] == "bound"


async def test_resolve_title_override(monkeypatch):
    m = _meeting("Dự án Mee")

    async def fake(session, user_id, q):
        return [m]

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake)

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="Mee"
    )
    assert out["meeting_id"] == str(m.id)
    assert out["resolved_by"] == "title"


async def test_resolve_ambiguous_picks_most_recent(monkeypatch):
    # repo returns most-recent first (ORDER BY created_at DESC); resolver takes [0].
    newer = _meeting("Mee Sprint 2")
    older = _meeting("Mee Sprint 1")

    async def fake(session, user_id, q):
        return [newer, older]

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake)

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="Mee"
    )
    assert out["meeting_id"] == str(newer.id)
    assert len(out["candidates"]) == 2


async def test_resolve_title_no_match_falls_back_to_bound(monkeypatch):
    async def fake(session, user_id, q):
        return []

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake)

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="Nonexistent"
    )
    assert out["meeting_id"] == "bound-id"
    assert out["resolved_by"] == "bound"
