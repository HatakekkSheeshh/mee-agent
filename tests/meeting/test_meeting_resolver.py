"""Pure LLM meeting-title resolver.

ILIKE substring search only matches when the query is INSIDE the title, so it
fails when the extracted phrase is longer than the title (title "GIP" is not a
substring of "meeting GIP có gì") or when titles are near-duplicates ("AI
Innovation Project" vs "AI Innovation Projects"). `llm_resolve_meeting` asks the
LLM to pick the best-matching meeting id from candidate titles (or NONE),
validating the chosen id is actually in the candidate set (never invents).

`generate(messages) -> str` is injected; tests mock it (no network). The titles
below mirror real rows from the `meetings` table.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from src.services.meeting_resolver import (
    build_meeting_match_messages,
    find_meeting_named_in,
    llm_resolve_meeting,
)


def _meeting(title: str):
    return SimpleNamespace(id=uuid.uuid4(), title=title)


# ---- build_meeting_match_messages (pure) ------------------------------------

def test_build_messages_includes_query_and_candidate_titles():
    m1 = _meeting("GIP")
    m2 = _meeting("AI Innovation Project")
    msgs = build_meeting_match_messages("meeting GIP có gì", [m1, m2])

    assert isinstance(msgs, list) and msgs
    assert all("role" in m and "content" in m for m in msgs)
    blob = "\n".join(m["content"] for m in msgs)
    assert "meeting GIP có gì" in blob
    assert m1.title in blob and m2.title in blob
    # Candidate ids must appear so the model can return one verbatim.
    assert str(m1.id) in blob and str(m2.id) in blob


# ---- llm_resolve_meeting ----------------------------------------------------

def test_resolve_matches_short_title_against_longer_phrase():
    # title "GIP" is NOT a substring of "meeting GIP có gì" → ILIKE misses it;
    # the LLM still picks it.
    gip = _meeting("GIP")
    other = _meeting("AI Innovation Project")

    def generate(messages):
        return json.dumps({"meeting_id": str(gip.id)})

    out = llm_resolve_meeting("meeting GIP có gì", [gip, other], generate=generate)
    assert out == str(gip.id)


def test_resolve_strips_think_then_parses():
    m = _meeting("Sprint Planning")

    def generate(messages):
        return f'<think>user means the sprint one</think>{{"meeting_id": "{m.id}"}}'

    out = llm_resolve_meeting("sprint", [m], generate=generate)
    assert out == str(m.id)


def test_resolve_none_when_model_says_none():
    m = _meeting("Sprint Planning")

    def generate(messages):
        return "NONE"

    out = llm_resolve_meeting("hoàn toàn không liên quan", [m], generate=generate)
    assert out is None


def test_resolve_rejects_id_not_in_candidates():
    m = _meeting("Sprint Planning")

    def generate(messages):
        # Model hallucinated an id that isn't a candidate.
        return json.dumps({"meeting_id": str(uuid.uuid4())})

    out = llm_resolve_meeting("sprint", [m], generate=generate)
    assert out is None


def test_resolve_empty_candidates_skips_generate():
    called = False

    def generate(messages):
        nonlocal called
        called = True
        return "anything"

    out = llm_resolve_meeting("anything", [], generate=generate)
    assert out is None
    assert called is False


def test_resolve_blank_query_returns_none():
    m = _meeting("Sprint Planning")
    out = llm_resolve_meeting("   ", [m], generate=lambda messages: "NONE")
    assert out is None


def test_resolve_generate_failure_returns_none():
    m = _meeting("Sprint Planning")

    def generate(messages):
        raise RuntimeError("llm down")

    out = llm_resolve_meeting("sprint", [m], generate=generate)
    assert out is None


# ---- find_meeting_named_in (deterministic — no LLM judgment) -----------------

def test_named_detects_distinct_meeting_over_current():
    # The live bug: bound to "AI Innovation Projects", the model merged "GIP" into
    # it. Deterministically, "GIP" in the message exactly names the GIP meeting —
    # a DIFFERENT row — so it must resolve to GIP, not the current meeting.
    gip = _meeting("GIP")
    current = _meeting("AI Innovation Projects")
    out = find_meeting_named_in(
        "meeting GIP có gì", [gip, current], current_meeting_id=str(current.id)
    )
    assert out == str(gip.id)


def test_named_returns_none_when_no_title_mentioned():
    gip = _meeting("GIP")
    current = _meeting("AI Innovation Projects")
    out = find_meeting_named_in(
        "tóm tắt cuộc họp tuần trước", [gip, current], current_meeting_id=str(current.id)
    )
    assert out is None


def test_named_matches_whole_token_not_substring():
    # "Test" must not match inside "Testing"/"latest" — avoids false positives.
    t = _meeting("Test")
    out = find_meeting_named_in("đang testing môi trường", [t])
    assert out is None


def test_named_prefers_longer_title_on_overlap():
    singular = _meeting("AI Innovation Project")
    plural = _meeting("AI Innovation Projects")
    out = find_meeting_named_in(
        "xem AI Innovation Projects", [singular, plural]
    )
    assert out == str(plural.id)


def test_named_case_insensitive():
    gip = _meeting("GIP")
    out = find_meeting_named_in("có gì trong gip không", [gip])
    assert out == str(gip.id)


def test_named_blank_or_empty_returns_none():
    gip = _meeting("GIP")
    assert find_meeting_named_in("   ", [gip]) is None
    assert find_meeting_named_in("GIP", []) is None
