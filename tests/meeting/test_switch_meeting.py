"""switch_meeting tool — resolve a project the user names by title.

Same flaw as resolve_meeting before the fix: ILIKE-only meant acronyms ("GIP")
returned not_found. Now: exactly-1 ILIKE → use it; 0 or >1 → LLM-resolve over the
user's meetings; no confident match → not_found WITH candidates so the agent can
offer near-matches instead of creating a new project.

Unit tests monkeypatch repo lookups + inject a fake `generate` (no DB, no network).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from src.services.tools import switch_meeting as sm

UID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _meeting(title: str):
    return SimpleNamespace(id=uuid.uuid4(), title=title)


def _boom(messages):
    raise AssertionError("LLM should not be called")


async def test_blank_title_errors():
    out = await sm.switch_meeting({"title": "  "}, session=object(), user_id=UID)
    assert "error" in out


async def test_single_ilike_match_fast_path(monkeypatch):
    m = _meeting("Dự án Mee")

    async def fake(session, user_id, q):
        return [m]

    monkeypatch.setattr(sm.repo, "find_meetings_by_title", fake)

    out = await sm.switch_meeting(
        {"title": "Mee"}, session=object(), user_id=UID, generate=_boom
    )
    assert out["status"] == "ok"
    assert out["meeting_id"] == str(m.id)


async def test_longer_phrase_resolves_via_llm(monkeypatch):
    # title "GIP" is NOT a substring of "meeting GIP có gì" → ILIKE returns [].
    gip = _meeting("GIP")
    other = _meeting("AI Innovation Project")

    async def fake_ilike(session, user_id, q):
        return []

    async def fake_list(session, user_id):
        return [gip, other]

    monkeypatch.setattr(sm.repo, "find_meetings_by_title", fake_ilike)
    monkeypatch.setattr(sm.repo, "list_meetings_for_user", fake_list)

    def generate(messages):
        return json.dumps({"meeting_id": str(gip.id)})

    out = await sm.switch_meeting(
        {"title": "meeting GIP có gì"}, session=object(), user_id=UID, generate=generate
    )
    assert out["status"] == "ok"
    assert out["meeting_id"] == str(gip.id)


async def test_no_match_returns_candidates(monkeypatch):
    m1 = _meeting("Dự án A")
    m2 = _meeting("Dự án B")

    async def fake_ilike(session, user_id, q):
        return []

    async def fake_list(session, user_id):
        return [m1, m2]

    monkeypatch.setattr(sm.repo, "find_meetings_by_title", fake_ilike)
    monkeypatch.setattr(sm.repo, "list_meetings_for_user", fake_list)

    out = await sm.switch_meeting(
        {"title": "Nonexistent"}, session=object(), user_id=UID,
        generate=lambda messages: "NONE",
    )
    assert out["status"] == "not_found"
    assert {c["id"] for c in out["candidates"]} == {str(m1.id), str(m2.id)}
