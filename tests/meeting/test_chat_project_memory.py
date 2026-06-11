"""Chat-agent recall of the distilled project-state projection.

Covers the read side of the AgentBase memory loop (network-free):
  - strip_project_marker: the agent sees the human body, not the bookkeeping marker;
  - _agent_system_prompt: the recalled state is injected as orientation when present,
    and absent (no stray block) when there's no memory.
"""
from __future__ import annotations

from meeting.memory_client import build_project_record_text, strip_project_marker
from meeting.graphs._chat_prompts import _agent_system_prompt


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
