"""Chat-agent recall of the distilled project-state projection.

Covers the read side of the AgentBase memory loop (network-free):
  - strip_project_marker: the agent sees the human body, not the bookkeeping marker;
  - _agent_system_prompt: the recalled state is injected as orientation when present,
    and absent (no stray block) when there's no memory;
  - is_record_stale + load_context staleness check (Q1): a recalled distillation
    whose marker hash differs from the meeting's CURRENT Postgres data is flagged
    (⚠ note) and a non-blocking bg re-sync is kicked — never blocking the turn.
"""
from __future__ import annotations

import uuid

import pytest

from meeting.memory_client import (
    STALE_NOTE,
    build_project_record_text,
    is_record_stale,
    strip_project_marker,
)
from meeting.graphs._chat_prompts import _agent_system_prompt
from meeting.graphs.chat_graph import context as ctx
from meeting.services.memory_sync import canonical_source_hash


# ── strip_project_marker ─────────────────────────────────────────────────

def test_strip_marker_removes_marker_keeps_title_and_body():
    rec = build_project_record_text("p1", "h1", "Trạng thái: đang chạy.", title="GIP")
    body = strip_project_marker(rec)
    assert "mee-sync" not in body and "hash=" not in body
    assert "# GIP" in body
    assert "Trạng thái: đang chạy." in body


def test_strip_marker_passthrough_for_unmarked_and_none():
    assert strip_project_marker("plain text") == "plain text"
    assert strip_project_marker(None) == ""


# ── system prompt injection ──────────────────────────────────────────────

def test_prompt_includes_project_memory_when_present():
    prompt = _agent_system_prompt({
        "meeting_context": {"title": "AI Innovation Project"},
        "project_memory": "Giai đoạn: tối ưu hóa. Blocker: API upload lỗi.",
    })
    assert "Trạng thái project" in prompt          # grounding-source header
    assert "Blocker: API upload lỗi." in prompt    # the recalled content
    # project_memory is the grounding source; action tools remain available
    assert "create_task" in prompt
    # retrieve (heavy RAG) stays detached — memory replaces it for Q&A grounding
    assert "retrieve" not in prompt


def test_prompt_omits_memory_block_when_absent():
    prompt = _agent_system_prompt({"meeting_context": {"title": "X"}})
    assert "Trạng thái project (bản chắt lọc" not in prompt


def test_prompt_nudges_remember_fact_capability():
    """The agent must know remember_fact exists so it stores durable facts the
    user asserts (e.g. 'gọi tôi là Ronaldo') instead of letting them evaporate."""
    prompt = _agent_system_prompt({"meeting_context": {"title": "X"}})
    assert "remember_fact" in prompt


# ── is_record_stale (pure, network-free) ─────────────────────────────────

def test_is_record_stale_true_when_marker_hash_differs_from_live():
    rec = build_project_record_text("p1", "OLDHASH", "Trạng thái cũ.", title="GIP")
    assert is_record_stale(rec, "LIVEHASH") is True


def test_is_record_stale_false_when_marker_hash_matches_live():
    rec = build_project_record_text("p1", "SAMEHASH", "Trạng thái.", title="GIP")
    assert is_record_stale(rec, "SAMEHASH") is False


def test_is_record_stale_false_for_unmarked_or_none():
    # No marker → freshness can't be proven; don't raise a false alarm.
    assert is_record_stale("plain recalled text", "LIVEHASH") is False
    assert is_record_stale(None, "LIVEHASH") is False


# ── load_context staleness wiring (Q1) ───────────────────────────────────
#
# Fakes for the repo seam — load_context only touches these attributes. The
# AgentBase browse (search_record) and the bg re-sync (schedule_resync) are
# injected via make_load_context so this is fully offline/non-blocking.

class _Rec:
    def __init__(self, mom_json, started_at, *, label="Phiên 1"):
        self.id = uuid.uuid4()
        self.title = label
        self.session_label = label
        self.purpose = None
        self.mom_json = mom_json
        self.started_at = started_at


class _Meeting:
    def __init__(self, recs, *, summary=None, title="AI Innovation Project"):
        self.id = uuid.uuid4()
        self.title = title
        self.project_summary_json = summary
        self.recordings = recs


def _patch_repo(monkeypatch, meeting):
    """Stub the repo calls load_context makes so it runs without a DB."""
    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    async def fake_get_meeting(session, mid):
        return meeting

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)
    monkeypatch.setattr(ctx.repo, "get_meeting", fake_get_meeting)


def _meeting_with_one_session():
    from datetime import datetime
    mom = {"decisions": ["Chốt v1"], "action_items": [], "blockers": []}
    rec = _Rec(mom, datetime(2026, 6, 1, 9, 0))
    return _Meeting([rec], summary={"narrative": "Đang chạy"}), rec


async def test_load_context_flags_stale_record_and_kicks_resync(monkeypatch):
    meeting, rec = _meeting_with_one_session()
    _patch_repo(monkeypatch, meeting)

    # Record carries a STALE marker hash (≠ the live data hash).
    record = {
        "memory": build_project_record_text(
            str(meeting.id), "STALE_HASH_FROM_OLD_SYNC",
            "Trạng thái (chỉ tới phiên cũ).", title=meeting.title,
        ),
        "created_at": "2026-06-01T00:00:00Z",
    }
    resync_calls: list[str] = []

    load_context = ctx.make_load_context(
        session=None,
        search_record=lambda pid: record,
        schedule_resync=lambda mid: resync_calls.append(mid),
    )
    out = await load_context(
        {"session_id": str(uuid.uuid4()), "meeting_id": str(meeting.id)}
    )

    # Honest now: the recalled body carries the staleness note…
    assert STALE_NOTE in out["project_memory"]
    assert "Trạng thái (chỉ tới phiên cũ)." in out["project_memory"]
    # …and a non-blocking bg re-sync was kicked for this meeting (self-heal next turn).
    assert resync_calls == [str(meeting.id)]


async def test_load_context_no_note_when_record_is_fresh(monkeypatch):
    meeting, rec = _meeting_with_one_session()
    _patch_repo(monkeypatch, meeting)

    # Record's marker hash == the live hash → fresh, no warning, no re-sync.
    live = canonical_source_hash(meeting.project_summary_json, [rec.mom_json])
    record = {
        "memory": build_project_record_text(
            str(meeting.id), live, "Trạng thái hiện tại.", title=meeting.title,
        ),
        "created_at": "2026-06-10T00:00:00Z",
    }
    resync_calls: list[str] = []

    load_context = ctx.make_load_context(
        session=None,
        search_record=lambda pid: record,
        schedule_resync=lambda mid: resync_calls.append(mid),
    )
    out = await load_context(
        {"session_id": str(uuid.uuid4()), "meeting_id": str(meeting.id)}
    )

    assert STALE_NOTE not in out["project_memory"]
    assert out["project_memory"] == "# AI Innovation Project\n\nTrạng thái hiện tại."
    assert resync_calls == []
