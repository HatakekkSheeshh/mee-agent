"""IdentityClient — pure helpers + faked-transport network (offline).

No live AgentBase. Network methods use an injected httpx.MockTransport that
serves both the IAM token URL and the request-key URL (mirrors
test_pm_agent_client's MockTransport pattern).
"""
from __future__ import annotations

import json

import httpx
import pytest

from meeting.services.identity_client import (
    IdentityClient,
    RequestKeyResult,
    build_request_key_url,
    parse_request_key_response,
)


def test_build_request_key_url():
    url = build_request_key_url(
        "https://agentbase.api.vngcloud.vn/identity/api/v1", "redmine", "mee"
    )
    assert url == (
        "https://agentbase.api.vngcloud.vn/identity/api/v1"
        "/outbound-auth/delegated-api-key-providers/redmine"
        "/agent-identities/mee/api-key"
    )


def test_parse_camelcase_completed_with_key():
    body = {"apiKey": "rk-secret", "status": "COMPLETED", "authorizationUrl": None}
    r = parse_request_key_response(body)
    assert r == RequestKeyResult(apikey="rk-secret", authorization_url=None, status="COMPLETED")


def test_parse_snakecase_in_progress_with_url():
    body = {"apikey": None, "authorization_url": "https://consent/x", "status": "IN_PROGRESS"}
    r = parse_request_key_response(body)
    assert r.apikey is None
    assert r.authorization_url == "https://consent/x"
    assert r.status == "IN_PROGRESS"


def test_parse_tolerates_missing_and_none_body():
    assert parse_request_key_response(None) == RequestKeyResult(None, None, None)
    assert parse_request_key_response({}) == RequestKeyResult(None, None, None)


def _handler(token_calls: list, key_body: dict):
    """MockTransport handler: serves the IAM token URL and the request-key URL."""
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "iam.api.vngcloud.vn":
            token_calls.append(request.headers.get("Authorization", ""))
            return httpx.Response(200, json={"access_token": "iam-tok", "expires_in": 3600})
        # request-key
        assert request.headers["Authorization"] == "Bearer iam-tok"
        body = json.loads(request.content.decode())
        assert body["agentUserId"]  # required, non-empty
        assert body["returnUrl"]    # required, non-empty
        return httpx.Response(200, json=key_body)
    return handle


def _client(handler) -> IdentityClient:
    return IdentityClient(
        base_url="https://agentbase.api.vngcloud.vn/identity/api/v1",
        agent_identity="mee",
        provider_name="redmine",
        client_id="cid",
        client_secret="csec",
        return_url="https://mee.example/redmine-callback",
        transport=httpx.MockTransport(handler),
    )


async def test_request_user_key_returns_key_when_completed():
    c = _client(_handler([], {"apikey": "rk-123", "status": "COMPLETED"}))
    r = await c.request_user_key("oid-abc")
    assert r.apikey == "rk-123"
    assert r.status == "COMPLETED"


async def test_get_user_key_returns_none_when_consent_pending():
    c = _client(_handler([], {"authorizationUrl": "https://consent", "status": "IN_PROGRESS"}))
    assert await c.get_user_key("oid-abc") is None


async def test_get_user_key_returns_key_string():
    c = _client(_handler([], {"apikey": "rk-xyz", "status": "COMPLETED"}))
    assert await c.get_user_key("oid-abc") == "rk-xyz"


def test_rejects_non_allowlisted_base_url():
    with pytest.raises(ValueError):
        IdentityClient(
            base_url="https://evil.example/identity/api/v1",
            agent_identity="mee", provider_name="redmine",
            client_id="c", client_secret="s", return_url="https://x",
        )


import meeting.services.identity_client as idc


def test_cache_hits_avoid_second_resolve(monkeypatch):
    idc.clear_key_cache()
    calls = {"n": 0}

    class _FakeIdentity:
        async def get_user_key(self, oid):
            calls["n"] += 1
            return "rk-cached"

    monkeypatch.setattr(idc, "get_identity_client", lambda: _FakeIdentity())
    fake_now = {"t": 1000.0}
    import asyncio

    async def go():
        k1 = await idc.get_cached_user_key("oid-1", now=lambda: fake_now["t"])
        k2 = await idc.get_cached_user_key("oid-1", now=lambda: fake_now["t"] + 10)  # within TTL
        return k1, k2

    k1, k2 = asyncio.run(go())
    assert k1 == k2 == "rk-cached"
    assert calls["n"] == 1  # second call served from cache


def test_bootstrap_payloads():
    from scripts.bootstrap_redmine_identity import identity_payload, provider_payload
    assert provider_payload("redmine") == {"name": "redmine"}
    p = identity_payload("mee", ["https://mee.example/redmine-callback"])
    assert p["name"] == "mee"
    assert p["allowedReturnUrls"] == ["https://mee.example/redmine-callback"]


def test_cache_expires_after_ttl(monkeypatch):
    idc.clear_key_cache()
    calls = {"n": 0}

    class _FakeIdentity:
        async def get_user_key(self, oid):
            calls["n"] += 1
            return "rk-cached"

    monkeypatch.setattr(idc, "get_identity_client", lambda: _FakeIdentity())
    import asyncio

    async def go():
        await idc.get_cached_user_key("oid-1", now=lambda: 1000.0, ttl=300)
        await idc.get_cached_user_key("oid-1", now=lambda: 1000.0 + 301, ttl=300)

    asyncio.run(go())
    assert calls["n"] == 2  # expired → re-resolved
