"""Unit tests for the agent-memory sync distillation layer (pure, no DB/network).

Covers the two network-free pieces of `meeting/services/memory_sync.py`:
  - canonical_source_hash: stable content hash used for change detection
    (skip re-distilling a project whose MoMs/summary are unchanged);
  - distill_project_state: assembles the LLM prompt from project_summary_json
    + recordings' mom_json and returns the condensed state text (LLM injected).

Spec: docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md
"""
from __future__ import annotations

from meeting.services.memory_sync import canonical_source_hash, distill_project_state


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
