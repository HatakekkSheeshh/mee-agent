"""Unit tests for the agent-memory sync distillation layer (pure, no DB/network).

Covers the two network-free pieces of `src/services/memory_sync.py`:
  - canonical_source_hash: stable content hash used for change detection
    (skip re-distilling a project whose MoMs/summary are unchanged);
  - distill_project_state: assembles the LLM prompt from project_summary_json
    + recordings' mom_json and returns the condensed state text (LLM injected).

Spec: docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md
"""
from __future__ import annotations

from src.services.memory_sync import (
    build_session_bullets,
    canonical_source_hash,
    distill_project_state,
    plan_project_sync,
    sync_one_project,
)


# ── build_session_bullets (deterministic, no LLM) ────────────────────────

def test_session_bullets_formats_decisions_actions_blockers():
    out = build_session_bullets([
        {"label": "Meeting 1", "date": "2026-06-08T09:00:00+00:00", "mom": {
            "decisions": ["ship Friday"],
            "action_items": [{"item": "viết migration", "pic": "Hiếu", "deadline": "10/06"}],
            "blockers": [{"text": "thiếu API key", "by": "An"}],
        }},
    ])
    assert "### Meeting 1 (2026-06-08)" in out
    assert "Quyết định: ship Friday" in out
    assert "Việc: viết migration — Hiếu (hạn 10/06)" in out
    assert "Blocker: thiếu API key (bởi An)" in out


def test_session_bullets_lists_itemless_session_with_placeholder():
    # Completeness: a session with no decisions/actions/blockers must still appear
    # in the roster (header + placeholder) so the agent knows it EXISTS and can
    # crawl it via recording_mom instead of denying it.
    out = build_session_bullets([
        {"label": "Meeting 3", "date": "2026-06-04T09:00:00+00:00", "mom": {}},
    ])
    assert "### Meeting 3 (2026-06-04)" in out
    assert "chưa ghi nhận" in out


def test_session_bullets_empty_list_returns_blank():
    assert build_session_bullets([]) == ""


def _session(n: int) -> dict:
    # Sessions are passed oldest→newest; each has content so it produces a block.
    return {"label": f"Phiên {n}", "date": f"2026-06-{n:02d}T09:00:00+00:00",
            "mom": {"decisions": [f"quyết định {n}"]}}


def test_session_bullets_windows_to_recent_and_marks_omitted():
    # 5 content sessions, window=2 → keep the 2 NEWEST (4,5), drop the 3 oldest,
    # and surface a collapse marker. Oldest detail lives in the LLM aggregate.
    out = build_session_bullets([_session(n) for n in range(1, 6)], window=2)

    assert "### Phiên 4" in out and "### Phiên 5" in out
    assert "### Phiên 1" not in out
    assert "### Phiên 2" not in out
    assert "### Phiên 3" not in out
    assert "3 phiên cũ hơn" in out  # omitted-count marker


def test_session_bullets_no_marker_when_within_window():
    out = build_session_bullets([_session(n) for n in range(1, 4)], window=8)
    assert "phiên cũ hơn" not in out
    assert out.count("###") == 3


def test_session_bullets_window_counts_all_sessions_including_itemless():
    # Itemless sessions are part of the roster and consume the recency window too,
    # so the oldest (here "Trống") collapses into the marker.
    sessions = [{"label": "Trống", "date": "2026-06-01T09:00:00+00:00", "mom": {}}] \
        + [_session(n) for n in (4, 5)]
    out = build_session_bullets(sessions, window=2)
    assert "### Trống" not in out
    assert "1 phiên cũ hơn" in out
    assert "### Phiên 4" in out and "### Phiên 5" in out


# ── canonical_source_hash ───────────────────────────────────────────────

def test_hash_is_stable_across_dict_key_order():
    summary_a = {"project_title": "X", "narrative": "did A then B"}
    summary_b = {"narrative": "did A then B", "project_title": "X"}

    assert canonical_source_hash(summary_a, []) == canonical_source_hash(summary_b, [])


def test_hash_changes_when_a_mom_changes():
    summary = {"project_title": "X"}
    moms_v1 = [{"decisions": ["ship Friday"]}]
    moms_v2 = [{"decisions": ["ship Monday"]}]

    assert canonical_source_hash(summary, moms_v1) != canonical_source_hash(summary, moms_v2)


def test_hash_stable_for_equal_inputs_including_none():
    # None summary + None entries must hash deterministically and equally.
    assert canonical_source_hash(None, [None]) == canonical_source_hash(None, [None])


def test_hash_is_hex_sha256():
    h = canonical_source_hash({"a": 1}, [])
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


# ── distill_project_state ───────────────────────────────────────────────

class _FakeLLM:
    """Minimal OpenAI-compatible stub: records the call, returns canned text."""

    def __init__(self, content: str):
        self._content = content
        self.calls: list[dict] = []
        self.chat = self  # so .chat.completions.create resolves
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Msg:
            content = self._content

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


def test_distill_returns_llm_content():
    llm = _FakeLLM("Trạng thái: đang triển khai. Blocker: chờ API.")
    out = distill_project_state(
        {"project_title": "Mee", "narrative": "n"},
        [{"decisions": ["d1"]}],
        client=llm,
        model="gemma",
    )
    assert out == "Trạng thái: đang triển khai. Blocker: chờ API."
    assert llm.calls, "LLM must be invoked"


def test_distill_strips_think_tags():
    llm = _FakeLLM("<think>reasoning here</think>Trạng thái cuối cùng.")
    out = distill_project_state({"project_title": "Mee"}, [], client=llm, model="gemma")
    assert "<think>" not in out and "reasoning" not in out
    assert out == "Trạng thái cuối cùng."


def test_distill_prompt_includes_source_content():
    llm = _FakeLLM("ok")
    distill_project_state(
        {"project_title": "AI Innovation", "narrative": "shipped v1"},
        [{"decisions": ["adopt pgvector"]}],
        client=llm,
        model="gemma",
    )
    sent = llm.calls[0]
    blob = str(sent.get("messages", ""))
    assert "AI Innovation" in blob
    assert "pgvector" in blob


# ── plan_project_sync ────────────────────────────────────────────────────

def test_plan_skips_when_hash_unchanged():
    summary = {"project_title": "X"}
    moms = [{"decisions": ["d1"]}]
    h = canonical_source_hash(summary, moms)
    action, out_hash = plan_project_sync(summary, moms, existing_hash=h)
    assert action == "skip"
    assert out_hash == h


def test_plan_syncs_when_hash_differs_or_absent():
    summary = {"project_title": "X"}
    moms = [{"decisions": ["d1"]}]
    assert plan_project_sync(summary, moms, existing_hash=None)[0] == "sync"
    assert plan_project_sync(summary, moms, existing_hash="stale")[0] == "sync"


def test_plan_empty_when_no_content():
    action, out_hash = plan_project_sync(None, [None], existing_hash=None)
    assert action == "empty"
    assert out_hash == ""


# ── sync_one_project (skip-path: zero distill/upsert when unchanged) ──────

def _recorder():
    calls = []
    def fn(*args, **kwargs):
        calls.append((args, kwargs))
        return "DISTILLED"
    fn.calls = calls
    return fn


def test_sync_skips_distill_and_upsert_when_hash_matches():
    summary = {"project_title": "X"}
    moms = [{"decisions": ["d1"]}]
    matching = canonical_source_hash(summary, moms)
    distill = _recorder()
    upsert = _recorder()

    result = sync_one_project(
        project_id="p1",
        project_summary=summary,
        moms=moms,
        get_existing_hash=lambda pid: matching,
        distill=distill,
        upsert_record=upsert,
    )
    assert result["action"] == "skip"
    assert distill.calls == [], "must NOT distill an unchanged project"
    assert upsert.calls == [], "must NOT upsert an unchanged project"


def test_sync_distills_and_upserts_when_changed():
    summary = {"project_title": "X"}
    moms = [{"decisions": ["d2"]}]
    distill = _recorder()
    upsert = _recorder()

    result = sync_one_project(
        project_id="p1",
        project_summary=summary,
        moms=moms,
        get_existing_hash=lambda pid: "stale",
        distill=distill,
        upsert_record=upsert,
    )
    assert result["action"] == "sync"
    assert result["text"] == "DISTILLED"
    assert len(distill.calls) == 1
    # upsert called with (project_id, distilled_text, source_hash)
    (args, _) = upsert.calls[0]
    assert args[0] == "p1" and args[1] == "DISTILLED"
    assert args[2] == canonical_source_hash(summary, moms)


def test_sync_dry_run_distills_but_does_not_upsert():
    summary = {"project_title": "X"}
    moms = [{"decisions": ["d3"]}]
    distill = _recorder()
    upsert = _recorder()

    result = sync_one_project(
        project_id="p1",
        project_summary=summary,
        moms=moms,
        get_existing_hash=lambda pid: None,
        distill=distill,
        upsert_record=upsert,
        dry_run=True,
    )
    assert result["action"] == "sync"
    assert len(distill.calls) == 1
    assert upsert.calls == [], "dry-run must not write"
