"""Kickoff role source — the FE supplies the role via env (no login in v1).

`_pick_role_name` decides the role name for a kickoff: the FE-provided role
(from VITE_KICKOFF_ROLE) wins; otherwise fall back to the AgentBase persona;
otherwise None (→ generic greeting).
"""
from __future__ import annotations

from meeting.api.chat import _pick_role_name


def test_request_role_wins_over_persona():
    assert _pick_role_name("AI Applied", "Business Analyst") == "AI Applied"


def test_blank_request_falls_back_to_persona():
    assert _pick_role_name("", "Business Analyst") == "Business Analyst"
    assert _pick_role_name(None, "Business Analyst") == "Business Analyst"
    assert _pick_role_name("   ", "Business Analyst") == "Business Analyst"


def test_both_absent_returns_none():
    assert _pick_role_name(None, None) is None
    assert _pick_role_name("", None) is None
