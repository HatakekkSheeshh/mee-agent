"""Unit tests for the project-state memory-records layer in meeting/memory_client.py.

Network-free: pure helpers (build/parse/select) plus the `search_project_record` /
`upsert_project_record` functions exercised through an injected `call` seam, so no
token, env, or HTTP is touched.

v1 is insert-only, newest-wins (record DELETE is denied for our SA) — see
docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md.
"""
from __future__ import annotations

from meeting.memory_client import (
    build_project_record_text,
    parse_project_marker,
    select_latest_project_record,
    search_project_record,
    upsert_project_record,
)


# ── build / parse marker (round-trip) ───────────────────────────────────────

def test_build_record_text_embeds_marker_and_state():
    text = build_project_record_text("p123", "abc123", "Trạng thái: đang chạy.")
    assert text.startswith("[mee-sync project=p123 hash=abc123]")
    assert "Trạng thái: đang chạy." in text


def test_parse_marker_round_trips_build():
    text = build_project_record_text("p123", "deadbeef", "state body")
    marker = parse_project_marker(text)
    assert marker == {"project_id": "p123", "hash": "deadbeef"}


def test_parse_marker_returns_none_for_unmarked_text():
    assert parse_project_marker("just some semantic fact, no marker") is None
    assert parse_project_marker(None) is None


# ── select latest (newest-wins) ──────────────────────────────────────────────

def test_select_latest_picks_newest_matching_project():
    records = [
        {"memory": build_project_record_text("p1", "old", "old state"),
         "created_at": "2026-06-10T10:00:00+00:00"},
        {"memory": build_project_record_text("p1", "new", "new state"),
         "created_at": "2026-06-11T10:00:00+00:00"},
        {"memory": build_project_record_text("p2", "other", "other proj"),
         "created_at": "2026-06-11T12:00:00+00:00"},
    ]
    latest = select_latest_project_record(records, "p1")
    assert parse_project_marker(latest["memory"])["hash"] == "new"


def test_select_latest_returns_none_when_no_project_match():
    records = [
        {"memory": build_project_record_text("pX", "h", "s"), "created_at": "2026-06-11T10:00:00+00:00"},
        {"memory": "unmarked record", "created_at": "2026-06-11T11:00:00+00:00"},
    ]
    assert select_latest_project_record(records, "p1") is None


# ── search_project_record (browse via injected call) ─────────────────────────

def test_search_returns_latest_record_for_project():
    browse_resp = {"listData": [
        {"memory": build_project_record_text("p1", "h1", "first"), "created_at": "2026-06-10T00:00:00+00:00"},
        {"memory": build_project_record_text("p1", "h2", "second"), "created_at": "2026-06-11T00:00:00+00:00"},
    ]}
    calls = []

    def fake_call(method, url, body, token):
        calls.append((method, url, body))
        return browse_resp

    rec = search_project_record("p1", memory_id="mem-1", token="t", call=fake_call)
    assert parse_project_marker(rec["memory"])["hash"] == "h2"
    # browse is a GET against the project_facts namespace, raw (no %2F)
    method, url, _ = calls[0]
    assert method == "GET"
    assert "memory-records?namespace=project_facts/mee-user" in url


def test_search_returns_none_without_memory_id(monkeypatch):
    monkeypatch.delenv("MEMORY_ID", raising=False)
    # call must never fire when memory_id is absent
    def boom(*a, **k):
        raise AssertionError("network must not be called")
    assert search_project_record("p1", token="t", call=boom) is None


# ── upsert_project_record (insert-directly via injected call) ────────────────

def test_upsert_posts_insert_directly_with_marker_body():
    sent = {}

    def fake_call(method, url, body, token):
        sent["method"] = method
        sent["url"] = url
        sent["body"] = body
        return {"ok": True}

    out = upsert_project_record("p1", "distilled state", "hashX",
                                memory_id="mem-1", token="t", call=fake_call)
    assert out == {"ok": True}
    assert sent["method"] == "POST"
    assert "memory-records:insert-directly?namespace=project_facts/mee-user" in sent["url"]
    # body shape is {"memoryRecords": ["<marker line>\n<text>"]}
    records = sent["body"]["memoryRecords"]
    assert len(records) == 1
    assert records[0].startswith("[mee-sync project=p1 hash=hashX]")
    assert "distilled state" in records[0]
