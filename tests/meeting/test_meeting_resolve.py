"""resolve_meeting — resolve which meeting the user means.

Default = the chat's bound meeting_id. If a title is named:
  - ILIKE fast-path: exactly 1 match → use it, no LLM.
  - 0 or >1 matches → fetch the user's meetings and LLM-resolve over their titles
    (handles acronyms ILIKE can't, e.g. "GIP" → "Giải pháp Internet Platform").
  - LLM finds nothing → fall back to bound, but return the user's meetings as
    `candidates` so the agent can offer near-matches instead of creating new.

Unit tests monkeypatch repo lookups + inject a fake `generate` (no DB, no network).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from src.graphs import chat_graph

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _meeting(title: str):
    return SimpleNamespace(id=uuid.uuid4(), title=title)


def _boom_generate(messages):  # must never be called on the fast paths
    raise AssertionError("LLM should not be called")


async def test_resolve_bound_default_when_no_title():
    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title=None,
        generate=_boom_generate,
    )
    assert out["meeting_id"] == "bound-id"
    assert out["resolved_by"] == "bound"


async def test_resolve_single_ilike_match_fast_path(monkeypatch):
    m = _meeting("Dự án Mee")

    async def fake(session, user_id, q):
        return [m]

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake)

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="Mee",
        generate=_boom_generate,  # exactly 1 ILIKE hit → no LLM
    )
    assert out["meeting_id"] == str(m.id)
    assert out["resolved_by"] == "title"


async def test_resolve_ambiguous_uses_llm(monkeypatch):
    # Near-duplicate real titles: ILIKE "AI Innovation Project" hits BOTH.
    singular = _meeting("AI Innovation Project")
    plural = _meeting("AI Innovation Projects")

    async def fake_ilike(session, user_id, q):
        return [singular, plural]

    async def fake_list(session, user_id):
        return [singular, plural]

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake_ilike)
    monkeypatch.setattr(chat_graph.repo, "list_meetings_for_user", fake_list)

    def generate(messages):
        return json.dumps({"meeting_id": str(plural.id)})

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id",
        title="AI Innovation Projects", generate=generate,
    )
    assert out["meeting_id"] == str(plural.id)
    assert out["resolved_by"] == "title"


async def test_resolve_longer_phrase_no_ilike_match_uses_llm(monkeypatch):
    # title "GIP" is NOT a substring of "meeting GIP có gì" → ILIKE returns [].
    gip = _meeting("GIP")
    other = _meeting("AI Innovation Project")

    async def fake_ilike(session, user_id, q):
        return []

    async def fake_list(session, user_id):
        return [gip, other]

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake_ilike)
    monkeypatch.setattr(chat_graph.repo, "list_meetings_for_user", fake_list)

    def generate(messages):
        return json.dumps({"meeting_id": str(gip.id)})

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="meeting GIP có gì",
        generate=generate,
    )
    assert out["meeting_id"] == str(gip.id)
    assert out["resolved_by"] == "title"


async def test_resolve_llm_none_falls_back_to_bound_with_candidates(monkeypatch):
    m1 = _meeting("Dự án A")
    m2 = _meeting("Dự án B")

    async def fake_ilike(session, user_id, q):
        return []

    async def fake_list(session, user_id):
        return [m1, m2]

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake_ilike)
    monkeypatch.setattr(chat_graph.repo, "list_meetings_for_user", fake_list)

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="Nonexistent",
        generate=lambda messages: "NONE",
    )
    assert out["meeting_id"] == "bound-id"
    assert out["resolved_by"] == "bound"
    # Candidates returned so the agent can offer near-matches, not "create new".
    assert {c["id"] for c in out["candidates"]} == {str(m1.id), str(m2.id)}


async def test_resolve_no_meetings_at_all(monkeypatch):
    async def fake_ilike(session, user_id, q):
        return []

    async def fake_list(session, user_id):
        return []

    monkeypatch.setattr(chat_graph.repo, "find_meetings_by_title", fake_ilike)
    monkeypatch.setattr(chat_graph.repo, "list_meetings_for_user", fake_list)

    out = await chat_graph.resolve_meeting(
        object(), user_id=UID, bound_meeting_id="bound-id", title="Whatever",
        generate=_boom_generate,  # no candidates → no LLM call
    )
    assert out["meeting_id"] == "bound-id"
    assert out["resolved_by"] == "bound"
    assert out["candidates"] == []
