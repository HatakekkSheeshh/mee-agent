"""Persona read — the user's role from AgentBase `user_prefs/{actorId}`.

`parse_user_role` is the pure newest-wins parser; `get_user_role` is the
best-effort network wrapper (injectable `call`, returns None on miss/error),
mirroring `search_project_record`.
"""
from __future__ import annotations

from src import memory_client as mc


# ─── parse_user_role (pure) ───────────────────────────────────────────

def test_parse_user_role_from_role_line():
    recs = [{"memory": "Tên: Anh\nrole: AI Applied\n", "created_at": "2026-06-10"}]
    assert mc.parse_user_role(recs) == "AI Applied"


def test_parse_user_role_vietnamese_label():
    recs = [{"memory": "vai trò: Business Analyst", "created_at": "2026-06-10"}]
    assert mc.parse_user_role(recs) == "Business Analyst"


def test_parse_user_role_picks_newest_record():
    recs = [
        {"memory": "role: AI Engineer", "created_at": "2026-06-01"},
        {"memory": "role: Software Engineer", "created_at": "2026-06-12"},
    ]
    assert mc.parse_user_role(recs) == "Software Engineer"


def test_parse_user_role_none_when_absent_or_empty():
    assert mc.parse_user_role([{"memory": "no role here", "created_at": "x"}]) is None
    assert mc.parse_user_role([]) is None


# ─── get_user_role (network seam) ─────────────────────────────────────

def test_get_user_role_uses_user_prefs_namespace_and_injected_call():
    captured = {}

    def fake_call(method, url, body, token):
        captured["method"] = method
        captured["url"] = url
        return {"data": [{"memory": "role: BA", "created_at": "2026-06-12"}]}

    out = mc.get_user_role("mee-user", memory_id="m-1", token="t", call=fake_call)
    assert out == "BA"
    assert captured["method"] == "GET"
    assert "user_prefs/mee-user" in captured["url"]


def test_get_user_role_none_without_memory_id():
    out = mc.get_user_role("mee-user", memory_id="", token="t", call=lambda *a: None)
    assert out is None


def test_get_user_role_swallows_errors_returns_none():
    def boom(*a):
        raise RuntimeError("network down")

    assert mc.get_user_role("mee-user", memory_id="m-1", token="t", call=boom) is None
