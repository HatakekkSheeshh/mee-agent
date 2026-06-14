"""PM_AGENT_AUTH_MODE switches how chat authenticates to pm-agent.

- "jwt" (default): forward the user's Microsoft Graph access token → pm-agent's
  JWT path. Requires real O365 login.
- "oid" (temporary): forward the user's raw Azure OID → pm-agent's direct-oid
  test port. Lets deploys without real login (mock) still drive pm-agent while
  that port is still open. Mock users (no ms_oid) return None, so the client
  falls back to the static TOKEN_AUTHEN_PM_AGENT OID.
"""
from __future__ import annotations

from types import SimpleNamespace

import meeting.api.chat as chat


async def test_oid_mode_forwards_user_oid_without_graph_call(monkeypatch):
    monkeypatch.setenv("PM_AGENT_AUTH_MODE", "oid")

    async def _boom(*a, **k):  # must NOT be called in oid mode
        raise AssertionError("get_graph_access_token should not run in oid mode")

    monkeypatch.setattr(chat, "get_graph_access_token", _boom)
    user = SimpleNamespace(ms_oid="11111111-2222-3333-4444-555555555555")

    assert await chat._graph_token_or_401(user, None) == user.ms_oid


async def test_oid_mode_mock_user_returns_none(monkeypatch):
    # ms_oid=None (mock user) → None → client falls back to static api_key OID.
    monkeypatch.setenv("PM_AGENT_AUTH_MODE", "oid")
    user = SimpleNamespace(ms_oid=None)
    assert await chat._graph_token_or_401(user, None) is None


async def test_oid_mode_non_guid_oid_falls_back_to_none(monkeypatch):
    # Legacy/dev rows (e.g. ms_oid="dev-local-user") are NOT valid Azure OIDs —
    # forwarding them hits pm-agent's static-key path → 401. Treat non-GUID as
    # None so the client falls back to the static TOKEN_AUTHEN_PM_AGENT OID.
    monkeypatch.setenv("PM_AGENT_AUTH_MODE", "oid")
    assert await chat._graph_token_or_401(SimpleNamespace(ms_oid="dev-local-user"), None) is None
    assert await chat._graph_token_or_401(SimpleNamespace(ms_oid="not-a-guid"), None) is None


async def test_jwt_mode_acquires_graph_token(monkeypatch):
    monkeypatch.setenv("PM_AGENT_AUTH_MODE", "jwt")

    async def _fake_token(u, s):
        return "header.payload.signature"

    monkeypatch.setattr(chat, "get_graph_access_token", _fake_token)
    user = SimpleNamespace(ms_oid="oid-guid")

    assert await chat._graph_token_or_401(user, None) == "header.payload.signature"


async def test_default_mode_is_jwt(monkeypatch):
    monkeypatch.delenv("PM_AGENT_AUTH_MODE", raising=False)

    async def _fake_token(u, s):
        return "jwt.token.here"

    monkeypatch.setattr(chat, "get_graph_access_token", _fake_token)
    user = SimpleNamespace(ms_oid="oid-guid")

    assert await chat._graph_token_or_401(user, None) == "jwt.token.here"
