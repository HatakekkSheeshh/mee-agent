"""AGENTBASE_REDMINE_RETURN_URL parsing.

The env may list several allowed return URLs (comma-separated, one per
deployment), but AgentBase Identity's `returnUrl` must be a SINGLE value. The
active deployment lists its URL first; `pick_return_url` returns that first
entry (a single value is returned unchanged). Sending the whole comma string
breaks the grant redirect.
"""
from __future__ import annotations

from src.services.identity_client import pick_return_url


def test_single_url_unchanged():
    assert pick_return_url("http://localhost:8001/app") == "http://localhost:8001/app"


def test_comma_list_returns_first():
    raw = "http://localhost:8001/app,https://prod.example.vn/app"
    assert pick_return_url(raw) == "http://localhost:8001/app"


def test_strips_whitespace_and_skips_empty_entries():
    assert pick_return_url("  ,  http://x/app , https://y/app") == "http://x/app"


def test_empty_returns_empty():
    assert pick_return_url("") == ""
    assert pick_return_url("  ,  ") == ""
