"""Unit tests for the chat-captured FACT records layer in src/memory_client.py.

Distinct from the project-state distillation blob (`[mee-sync …]`): a remembered
fact carries a `[mee-fact scope=… author=… session=…]` marker, is written to a
scope-specific namespace (read==write), and is recalled as plain text into the
chat prompt. Network-free: pure helpers plus insert/list exercised through an
injected `call` seam.

Spec: docs/superpowers/specs/2026-06-16-chat-knowledge-capture-design.md
v1 is insert-only, newest-wins (record DELETE is denied for our SA).
"""
from __future__ import annotations

from src import memory_client as mc


# ── build / parse fact marker (round-trip) ──────────────────────────────────

def test_build_fact_record_embeds_marker_and_text():
    text = mc.build_fact_record_text(
        "Gọi user là Ronaldo.", scope="user", author_oid="oid-1", session_id="sid-1"
    )
    assert text.startswith("[mee-fact scope=user ")
    assert "active=1" in text and "author=oid-1" in text and "session=sid-1" in text
    assert "Gọi user là Ronaldo." in text


def test_parse_fact_marker_round_trips_build():
    text = mc.build_fact_record_text(
        "Deadline dời sang 30/06.", scope="project", author_oid="oid-9", session_id="sid-2"
    )
    marker = mc.parse_fact_marker(text)
    assert marker == {
        "scope": "project",
        "key": mc.fact_key("Deadline dời sang 30/06."),
        "active": True,
        "author": "oid-9",
        "session": "sid-2",
    }


def test_parse_fact_marker_legacy_without_key_active_defaults_active():
    legacy = "[mee-fact scope=user author=o session=s]\nGọi user là Ronaldo."
    marker = mc.parse_fact_marker(legacy)
    assert marker["active"] is True and marker["key"] is None


def test_fact_key_normalizes_case_and_whitespace():
    assert mc.fact_key("Gọi user là Ronaldo.") == mc.fact_key("  gọi  user là   ronaldo.  ")


def test_parse_fact_marker_none_for_unmarked_or_sync_record():
    assert mc.parse_fact_marker("just a plain fact") is None
    assert mc.parse_fact_marker(None) is None
    # a project-state distillation blob is NOT a fact record
    sync_blob = mc.build_project_record_text("p1", "h1", "state body")
    assert mc.parse_fact_marker(sync_blob) is None


# ── namespace resolution (read==write, per the actor-granularity decision) ───

def test_fact_namespace_user_scope_uses_user_prefs_and_ms_oid():
    assert mc.fact_namespace("user", "oid-abc") == "user_prefs/oid-abc"


def test_fact_namespace_project_scope_partitions_by_meeting_id():
    assert mc.fact_namespace("project", "mtg-123") == "project_facts/mtg-123"


# ── insert_fact_record (insert-directly via injected call) ───────────────────

def test_insert_fact_record_posts_insert_directly_with_marker_body():
    sent = {}

    def fake_call(method, url, body, token):
        sent.update(method=method, url=url, body=body)
        return {"ok": True}

    out = mc.insert_fact_record(
        "Gọi user là Ronaldo.",
        namespace="user_prefs/oid-1",
        scope="user",
        author_oid="oid-1",
        session_id="sid-1",
        memory_id="mem-1",
        token="t",
        call=fake_call,
    )
    assert out == {"ok": True}
    assert sent["method"] == "POST"
    assert "memory-records:insert-directly?namespace=user_prefs/oid-1" in sent["url"]
    records = sent["body"]["memoryRecords"]
    assert len(records) == 1
    assert records[0].startswith("[mee-fact scope=user ")
    assert "active=1" in records[0] and "author=oid-1" in records[0]
    assert "Gọi user là Ronaldo." in records[0]


def test_insert_fact_record_raises_without_memory_id(monkeypatch):
    monkeypatch.delenv("MEMORY_ID", raising=False)

    def boom(*a, **k):
        raise AssertionError("network must not fire without MEMORY_ID")

    try:
        mc.insert_fact_record(
            "x", namespace="user_prefs/o", scope="user", token="t", call=boom
        )
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError when MEMORY_ID is unset")


# ── list_fact_records (browse, newest-first, fact-only) ──────────────────────

def test_list_fact_records_returns_bodies_newest_first():
    browse = {"listData": [
        {"memory": mc.build_fact_record_text("Tên là Ronaldo.", scope="user",
                                             author_oid="o", session_id="s"),
         "created_at": "2026-06-10T00:00:00+00:00"},
        {"memory": mc.build_fact_record_text("Tên là Messi.", scope="user",
                                             author_oid="o", session_id="s"),
         "created_at": "2026-06-12T00:00:00+00:00"},
    ]}

    def fake_call(method, url, body, token):
        return browse

    bodies = mc.list_fact_records("user_prefs/o", memory_id="m", token="t", call=fake_call)
    assert bodies == ["Tên là Messi.", "Tên là Ronaldo."]


def test_list_fact_records_ignores_non_fact_records():
    browse = {"listData": [
        {"memory": mc.build_project_record_text("p1", "h", "distilled state"),
         "created_at": "2026-06-12T00:00:00+00:00"},
        {"memory": mc.build_fact_record_text("Một fact thật.", scope="project",
                                             author_oid="o", session_id="s"),
         "created_at": "2026-06-11T00:00:00+00:00"},
    ]}

    def fake_call(method, url, body, token):
        return browse

    bodies = mc.list_fact_records("project_facts/p1", memory_id="m", token="t", call=fake_call)
    assert bodies == ["Một fact thật."]


def test_list_fact_records_hides_forgotten_fact_newest_wins():
    key = mc.fact_key("Gọi user là Ronaldo.")
    browse = {"listData": [
        {"memory": mc.build_fact_record_text("Gọi user là Ronaldo.", scope="user",
                                             active=True, key=key, author_oid="o", session_id="s"),
         "created_at": "2026-06-10T00:00:00+00:00"},
        {"memory": mc.build_fact_record_text("Gọi user là Ronaldo.", scope="user",
                                             active=False, key=key, author_oid="o", session_id="s"),
         "created_at": "2026-06-12T00:00:00+00:00"},  # newer tombstone wins → hidden
    ]}
    bodies = mc.list_fact_records("user_prefs/o", memory_id="m", token="t",
                                  call=lambda *a: browse)
    assert bodies == []


def test_list_fact_records_reactivation_newest_wins():
    key = mc.fact_key("Gọi user là Ronaldo.")
    browse = {"listData": [
        {"memory": mc.build_fact_record_text("Gọi user là Ronaldo.", scope="user",
                                             active=False, key=key, author_oid="o", session_id="s"),
         "created_at": "2026-06-10T00:00:00+00:00"},
        {"memory": mc.build_fact_record_text("Gọi user là Ronaldo.", scope="user",
                                             active=True, key=key, author_oid="o", session_id="s"),
         "created_at": "2026-06-12T00:00:00+00:00"},  # newer re-assert wins → visible
    ]}
    bodies = mc.list_fact_records("user_prefs/o", memory_id="m", token="t",
                                  call=lambda *a: browse)
    assert bodies == ["Gọi user là Ronaldo."]


def test_list_fact_records_empty_without_memory_id(monkeypatch):
    monkeypatch.delenv("MEMORY_ID", raising=False)
    assert mc.list_fact_records("user_prefs/o", token="t", call=lambda *a: None) == []


# ── parse_user_role hardening: a newer [mee-fact] record must not hide role ──

def test_parse_user_role_skips_fact_records_without_role_line():
    recs = [
        {"memory": "role: AI Engineer", "created_at": "2026-06-01"},
        {"memory": mc.build_fact_record_text("Tên là Ronaldo.", scope="user",
                                             author_oid="o", session_id="s"),
         "created_at": "2026-06-16"},  # newest, but no role line
    ]
    assert mc.parse_user_role(recs) == "AI Engineer"
