# Per-user Redmine key via AgentBase Identity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the process-global `REDMINE_API_KEY` with each VNG user's own Redmine key, retrieved at tool-execution time from GreenNode AgentBase Identity's `delegated` API-key provider (keyed by the user's Azure OID), and surface a red warning banner + redirect consent gate when Redmine or pm-agent is unreachable.

**Architecture:** A new async `IdentityClient` (`meeting/services/identity_client.py`) talks to the AgentBase Identity service (IAM client-credentials token → `request-key` on the `redmine` delegated provider under a dedicated `mee` agent identity). At tool time, `redmine.py::_proxy` resolves the current user's `ms_oid` → TTL-cached key → per-call Bearer into the MCP client; a missing key returns the structured `{"error": "redmine_key_missing"}` that the loop-side guard (`f67e318`) already handles. A new `GET /api/redmine/status` route powers a post-login probe; the React `RedmineStatusBanner` shows red warnings and opens the AgentBase consent gate. A one-time `scripts/bootstrap_redmine_identity.py` provisions the `mee` identity + `redmine` provider.

**Tech Stack:** Python 3.12, FastAPI, httpx (async, with `httpx.MockTransport` in tests — mirrors `pm_agent_client`), stdlib `urllib` is the existing IAM-token precedent in `memory_client.py`, pytest (`asyncio_mode=auto`), React 18 + TS + Vite (no FE test runner → manual verification).

---

## Probe findings (resolved before planning — do not re-probe)

These were confirmed live against AgentBase + the prod DB on 2026-06-15:

1. **`request-key` contract is hybrid.** `POST /outbound-auth/delegated-api-key-providers/{provider}/agent-identities/{agentName}/api-key` with body `{agentUserId, returnUrl}` returns a result carrying **`apikey`** (the key, if the user already authorized), **`authorization_url`** (hosted consent URL to redirect to otherwise), and **`status`** (`IN_PROGRESS` | `COMPLETED` | `FAILED`). SDK attribute names are snake_case; the raw REST JSON field names were **not** live-confirmed (a real request-key call needs the provider to exist + returnUrl whitelisted + triggers a federation flow). **Therefore the response parser MUST tolerate both camelCase and snake_case** (`apikey`/`apiKey`/`api_key`, `authorization_url`/`authorizationUrl`). The bootstrap + first live run confirm the exact spelling — the tolerant parser is the safeguard.
2. **No `redmine` delegated provider exists yet** (`content: []`). Must be created once (Task 7).
3. **Only one agent identity exists** — the auto-generated `runtime-f0011260-…` with `allowedReturnUrls: null`. Decision (user-confirmed): create a **dedicated `mee` agent identity** with `allowedReturnUrls` from env (Task 7), rather than reuse the runtime one.
4. **`ms_oid` is the OID column** (`users.ms_oid`, not `oid`). 4/7 prod users have it (all real O365 users); 3 mock/dev users are NULL → key resolution must fail gracefully to `redmine_key_missing`, with an opt-in dev fallback to the env key behind `REDMINE_DEV_FALLBACK`.
5. **IAM token endpoint + flow** is already proven in `meeting/memory_client.py::_get_token` — `POST https://iam.api.vngcloud.vn/accounts-api/v2/auth/token`, Basic `client_id:client_secret`, `grant_type=client_credentials`. Reuse the pattern (async httpx here for testability).
6. **`user_id` threaded into tools is `str(user.id)`** (the UUID PK), and `_proxy._exec(args, *, session, user_id)` also receives the DB `session` — so OID is resolved via `await session.get(User, uuid.UUID(user_id))`.

## Test command

```bash
DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q
```
Baseline before this work: **336 passed**. Single file/test: append the path, e.g. `… venv/bin/pytest tests/meeting/test_identity_client.py -v`. The repo venv is `venv/` (NOT `.venv/`). Do **not** run `alembic upgrade head` (DB has drift; no schema changes in this plan).

---

## File structure

**Backend (new):**
- `meeting/services/identity_client.py` — `IdentityClient`, pure helpers (`parse_request_key_response`, `build_request_key_url`), `RequestKeyResult`, singleton `get_identity_client()`, TTL cache `get_cached_user_key()`.
- `meeting/api/redmine.py` — `GET /api/redmine/status` route + pure `build_redmine_status(...)`; `EXPECTED_REDMINE_TOOL_COUNT` constant.
- `scripts/bootstrap_redmine_identity.py` — one-shot provisioning (idempotent).

**Backend (modified):**
- `meeting/services/redmine_mcp_client.py` — per-call `api_key` on `call_tool`/`_session`; extract pure `_auth_headers`.
- `meeting/services/tools/redmine.py` — `resolve_redmine_key()` + `_proxy._exec` per-user resolution.
- `meeting/app.py` — register the redmine router.
- `.env.example` — new env vars.

**Frontend (modified/new):**
- `meeting_frontend_react/src/api/client.ts` — `redmine.status()`.
- `meeting_frontend_react/src/components/RedmineStatusBanner.tsx` (new).
- `meeting_frontend_react/src/App.tsx` — fetch status post-auth, render banner, gate redirect + re-probe.
- `meeting_frontend_react/src/i18n.ts` — VI/EN strings.

**Tests (new):**
- `tests/meeting/test_identity_client.py`
- `tests/meeting/test_redmine_status.py`
- extend `tests/meeting/test_tools_redmine.py` (proxy per-user behavior)
- new pure-header test in `tests/meeting/test_redmine_mcp_headers.py`

---

## Phase 1 — IdentityClient

### Task 1: Pure helpers — parse `request-key` response + build URL

**Files:**
- Create: `meeting/services/identity_client.py`
- Test: `tests/meeting/test_identity_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_identity_client.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -v`
Expected: FAIL — `ModuleNotFoundError: meeting.services.identity_client`.

- [ ] **Step 3: Write minimal implementation**

```python
# meeting/services/identity_client.py
"""Thin async client for GreenNode AgentBase Identity (outbound-auth).

Resolves each end-user's own Redmine API key from the `delegated` API-key
provider, keyed by the user's Azure OID (agentUserId = users.ms_oid). Auth to
AgentBase is IAM client-credentials (GREENNODE_CLIENT_ID/SECRET), the same flow
proven in meeting/memory_client.py — here async (httpx) with an injectable
transport so the parsing/URL logic is unit-tested offline.

The raw Redmine key never passes through Mee's chat/LLM/logs: get_user_key only
returns it to the per-call MCP Bearer; the consent flow is a hosted redirect.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ALLOWED_IDENTITY_HOST = "agentbase.api.vngcloud.vn"
IAM_TOKEN_URL = "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"
DEFAULT_IDENTITY_BASE = "https://agentbase.api.vngcloud.vn/identity/api/v1"


@dataclass(frozen=True)
class RequestKeyResult:
    apikey: Optional[str]
    authorization_url: Optional[str]
    status: Optional[str]


def _first(body: dict, *keys: str):
    for k in keys:
        if k in body and body[k] is not None:
            return body[k]
    return None


def parse_request_key_response(body: Optional[dict]) -> RequestKeyResult:
    """Tolerant parse of the request-key response (camelCase OR snake_case)."""
    body = body or {}
    return RequestKeyResult(
        apikey=_first(body, "apikey", "apiKey", "api_key"),
        authorization_url=_first(body, "authorization_url", "authorizationUrl", "authorizationURL"),
        status=_first(body, "status"),
    )


def build_request_key_url(base_url: str, provider_name: str, agent_identity: str) -> str:
    base = base_url.rstrip("/")
    return (
        f"{base}/outbound-auth/delegated-api-key-providers/{provider_name}"
        f"/agent-identities/{agent_identity}/api-key"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/identity_client.py tests/meeting/test_identity_client.py
git commit -m "feat(identity): pure request-key parser + URL builder for AgentBase Identity"
```

---

### Task 2: IdentityClient network — token + request_user_key + get_user_key

**Files:**
- Modify: `meeting/services/identity_client.py`
- Test: `tests/meeting/test_identity_client.py`

- [ ] **Step 1: Write the failing test** (append to the file)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -k "request_user_key or get_user_key or allowlisted" -v`
Expected: FAIL — `IdentityClient` has no such constructor/methods.

- [ ] **Step 3: Write minimal implementation** (append to `identity_client.py`)

```python
class IdentityClient:
    def __init__(
        self,
        *,
        base_url: str,
        agent_identity: str,
        provider_name: str,
        client_id: str,
        client_secret: str,
        return_url: str,
        timeout: float = 20.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if ALLOWED_IDENTITY_HOST not in (base_url or ""):
            raise ValueError(f"Identity base_url must be on {ALLOWED_IDENTITY_HOST}: {base_url!r}")
        self._base_url = base_url.rstrip("/")
        self._agent_identity = agent_identity
        self._provider = provider_name
        self._client_id = client_id
        self._client_secret = client_secret
        self._return_url = return_url
        self._timeout = timeout
        self._transport = transport
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and self._token_exp > now + 60:
            return self._token
        if not self._client_id or not self._client_secret:
            raise RuntimeError("Missing GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET")
        auth = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(
                IAM_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = now + float(data.get("expires_in", 3600))
        return self._token

    async def request_user_key(
        self, agent_user_id: str, return_url: Optional[str] = None
    ) -> RequestKeyResult:
        token = await self._get_token()
        url = build_request_key_url(self._base_url, self._provider, self._agent_identity)
        payload = {"agentUserId": agent_user_id, "returnUrl": return_url or self._return_url}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(
                url, json=payload, headers={"Authorization": f"Bearer {token}"}
            )
        resp.raise_for_status()
        return parse_request_key_response(resp.json())

    async def get_user_key(self, agent_user_id: str) -> Optional[str]:
        """The user's stored Redmine key, or None if not yet authorized."""
        try:
            return (await self.request_user_key(agent_user_id)).apikey
        except Exception as e:  # fail closed; never leak / never crash the turn
            logger.warning("identity get_user_key failed for %s: %s", agent_user_id, e)
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/identity_client.py tests/meeting/test_identity_client.py
git commit -m "feat(identity): async IdentityClient (IAM token + delegated request-key)"
```

---

### Task 3: Singleton + TTL key cache

**Files:**
- Modify: `meeting/services/identity_client.py`
- Test: `tests/meeting/test_identity_client.py`

- [ ] **Step 1: Write the failing test** (append)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -k cache -v`
Expected: FAIL — `clear_key_cache` / `get_cached_user_key` / `get_identity_client` undefined.

- [ ] **Step 3: Write minimal implementation** (append)

```python
_singleton: Optional[IdentityClient] = None
_key_cache: dict[str, tuple[Optional[str], float]] = {}
DEFAULT_KEY_TTL = 300.0  # seconds


def get_identity_client() -> IdentityClient:
    """Lazy env singleton (mirrors get_redmine_mcp_client / get_pm_agent_client)."""
    global _singleton
    if _singleton is None:
        _singleton = IdentityClient(
            base_url=os.getenv("AGENTBASE_IDENTITY_URL", DEFAULT_IDENTITY_BASE),
            agent_identity=os.getenv("AGENTBASE_AGENT_IDENTITY", "mee"),
            provider_name=os.getenv("REDMINE_DELEGATED_PROVIDER", "redmine"),
            client_id=os.getenv("GREENNODE_CLIENT_ID", ""),
            client_secret=os.getenv("GREENNODE_CLIENT_SECRET", ""),
            return_url=os.getenv("AGENTBASE_REDMINE_RETURN_URL", ""),
        )
    return _singleton


def clear_key_cache() -> None:
    _key_cache.clear()


async def get_cached_user_key(agent_user_id: str, *, now=time.time, ttl: float = DEFAULT_KEY_TTL) -> Optional[str]:
    """TTL-cached per-user key resolution (negative results cached too)."""
    hit = _key_cache.get(agent_user_id)
    t = now()
    if hit is not None and hit[1] > t:
        return hit[0]
    key = await get_identity_client().get_user_key(agent_user_id)
    _key_cache[agent_user_id] = (key, t + ttl)
    return key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/identity_client.py tests/meeting/test_identity_client.py
git commit -m "feat(identity): TTL-cached per-user key resolution + env singleton"
```

---

## Phase 2 — Per-user key at tool execution

### Task 4: Per-call api_key on the Redmine MCP client

**Files:**
- Modify: `meeting/services/redmine_mcp_client.py`
- Test: `tests/meeting/test_redmine_mcp_headers.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_redmine_mcp_headers.py
"""The MCP client builds its Authorization header from a per-call key when
given one, else the constructed (env) key. Pure header logic — no network."""
from meeting.services.redmine_mcp_client import RedmineMcpClient


def _client():
    return RedmineMcpClient(base_url="https://mcp.example/mcp", api_key="env-key")


def test_auth_headers_uses_per_call_key():
    c = _client()
    assert c._auth_headers("user-key") == {"Authorization": "Bearer user-key"}


def test_auth_headers_falls_back_to_env_key():
    c = _client()
    assert c._auth_headers(None) == {"Authorization": "Bearer env-key"}


def test_auth_headers_empty_when_no_key():
    c = RedmineMcpClient(base_url="https://mcp.example/mcp", api_key="")
    assert c._auth_headers(None) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_redmine_mcp_headers.py -v`
Expected: FAIL — `RedmineMcpClient` has no `_auth_headers`.

- [ ] **Step 3: Write minimal implementation**

In `meeting/services/redmine_mcp_client.py`, add the pure helper and thread `api_key` through `_session` / `call_tool`. Replace the `_session` method and `call_tool` with:

```python
    def _auth_headers(self, api_key: Optional[str]) -> dict:
        """Bearer from the per-call key, else the env key; empty if neither."""
        key = api_key or self._api_key
        return {"Authorization": f"Bearer {key}"} if key else {}

    @asynccontextmanager
    async def _session(self, api_key: Optional[str] = None):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = self._auth_headers(api_key)
        async with streamablehttp_client(self._url, headers=headers, timeout=self._timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def call_tool(self, name: str, arguments: dict, *, api_key: Optional[str] = None) -> dict:
        logger.info("[redmine-mcp] call_tool %s args=%s", name, arguments)
        try:
            async with self._session(api_key=api_key) as session:
                result = await session.call_tool(name, arguments)
        except Exception as e:
            logger.exception("[redmine-mcp] call_tool %s failed", name)
            return {"error": f"redmine mcp error: {e}"}
        return _parse_call_result(result)
```

(`fetch_redmine_tool_schemas` in `redmine.py` calls `client._session()` with no arg — still valid since `api_key` defaults to `None`, using the env key for discovery.)

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_redmine_mcp_headers.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/redmine_mcp_client.py tests/meeting/test_redmine_mcp_headers.py
git commit -m "feat(redmine): per-call api_key override on the MCP client"
```

---

### Task 5: Per-user key resolution in `_proxy`

**Files:**
- Modify: `meeting/services/tools/redmine.py`
- Test: `tests/meeting/test_tools_redmine.py` (extend + update existing proxy test)

- [ ] **Step 1: Write the failing tests** (replace `test_registered_executor_proxies_to_client` and add new cases at the end of `tests/meeting/test_tools_redmine.py`)

```python
async def test_registered_executor_forwards_resolved_user_key(monkeypatch):
    redmine.register_redmine_tools(FAKE_SCHEMAS)
    captured = {}

    class _FakeClient:
        async def call_tool(self, name, arguments, *, api_key=None):
            captured["name"] = name
            captured["args"] = arguments
            captured["api_key"] = api_key
            return {"ok": True}

    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _FakeClient())

    async def _fake_resolve(user_id, session):
        return "rk-user-123"

    monkeypatch.setattr(redmine, "resolve_redmine_key", _fake_resolve)
    executor = tools.get_tool("get_overdue_issues")["executor"]
    out = await executor({"project_name": "GIP"}, session=None, user_id="u1")
    assert out == {"ok": True}
    assert captured == {"name": "get_overdue_issues", "args": {"project_name": "GIP"}, "api_key": "rk-user-123"}


async def test_registered_executor_missing_key_returns_structured_error(monkeypatch):
    redmine.register_redmine_tools(FAKE_SCHEMAS)

    async def _no_key(user_id, session):
        return None

    monkeypatch.setattr(redmine, "resolve_redmine_key", _no_key)
    # Client must NOT be called when the key is missing.
    monkeypatch.setattr(redmine, "get_redmine_mcp_client",
                        lambda: (_ for _ in ()).throw(AssertionError("must not call client")))
    executor = tools.get_tool("get_overdue_issues")["executor"]
    out = await executor({"project_name": "GIP"}, session=None, user_id="u1")
    assert out == {"error": "redmine_key_missing"}


def test_resolve_redmine_key_dev_fallback(monkeypatch):
    import asyncio
    monkeypatch.setenv("REDMINE_DEV_FALLBACK", "1")
    monkeypatch.setenv("REDMINE_API_KEY", "env-dev-key")
    # No oid lookup needed when the dev fallback is on.
    out = asyncio.run(redmine.resolve_redmine_key(user_id=None, session=None))
    assert out == "env-dev-key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_tools_redmine.py -v`
Expected: FAIL — `redmine.resolve_redmine_key` undefined; old proxy returns the raw client result without an `api_key`.

- [ ] **Step 3: Write minimal implementation**

In `meeting/services/tools/redmine.py`, add imports and the resolver, and rewrite `_proxy`:

```python
import os
import uuid

from meeting.db.models import User
from meeting.services.identity_client import get_cached_user_key


def _dev_fallback_enabled() -> bool:
    return os.getenv("REDMINE_DEV_FALLBACK", "").strip().lower() in ("1", "true", "yes")


async def _oid_for_user(user_id, session) -> Optional[str]:
    if not user_id or session is None:
        return None
    try:
        user = await session.get(User, uuid.UUID(str(user_id)))
    except Exception:  # malformed id / detached session
        return None
    return user.ms_oid if user else None


async def resolve_redmine_key(user_id, session) -> Optional[str]:
    """The current user's Redmine key: dev fallback → OID → cached AgentBase key."""
    if _dev_fallback_enabled():
        env_key = os.getenv("REDMINE_API_KEY", "")
        if env_key:
            return env_key
    oid = await _oid_for_user(user_id, session)
    if not oid:
        return None
    return await get_cached_user_key(oid)


def _proxy(name: str):
    async def _exec(args: dict, *, session, user_id) -> dict:
        key = await resolve_redmine_key(user_id, session)
        if not key:
            return {"error": "redmine_key_missing"}
        return await get_redmine_mcp_client().call_tool(name, dict(args or {}), api_key=key)

    _exec.__name__ = f"redmine_{name}"
    return _exec
```

(`Optional` is already imported in `redmine.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_tools_redmine.py -v`
Expected: PASS (all, including the rewritten proxy tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/tools/redmine.py tests/meeting/test_tools_redmine.py
git commit -m "feat(redmine): resolve per-user key at tool time (dev fallback → OID → AgentBase)"
```

---

## Phase 3 — Status endpoint

### Task 6: `build_redmine_status` (pure) + `GET /api/redmine/status` route

**Files:**
- Create: `meeting/api/redmine.py`
- Modify: `meeting/app.py:522-528` (router registration)
- Test: `tests/meeting/test_redmine_status.py`

> Following the repo's API-test convention (`test_chat_api_pm.py`): the route is thin and delegates to a **pure** `build_redmine_status(...)`; that pure function is unit-tested, the route is verified in the manual smoke (Task 11). No live FastAPI TestClient/DB.

- [ ] **Step 1: Write the failing test**

```python
# tests/meeting/test_redmine_status.py
"""Pure status-envelope builder for GET /api/redmine/status."""
from meeting.api.redmine import EXPECTED_REDMINE_TOOL_COUNT, build_redmine_status


def test_all_ok_no_banner_no_gate():
    s = build_redmine_status(
        key_present=True, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT,
        pm_agent_ok=True, gate_url=None,
    )
    assert s["redmine_key_present"] is True
    assert s["redmine_tools_ok"] is True
    assert s["pm_agent_ok"] is True
    assert s["gate_url"] is None
    assert s["all_ok"] is True


def test_missing_key_sets_gate_and_not_ok():
    s = build_redmine_status(
        key_present=False, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT,
        pm_agent_ok=True, gate_url="https://consent/x",
    )
    assert s["redmine_key_present"] is False
    assert s["redmine_tools_ok"] is False  # no key → tools cannot work
    assert s["gate_url"] == "https://consent/x"
    assert s["all_ok"] is False


def test_tool_count_mismatch_reported():
    s = build_redmine_status(
        key_present=True, registered_tool_count=3,
        pm_agent_ok=True, gate_url=None,
    )
    assert s["redmine_tools_ok"] is False
    assert s["registered_tool_count"] == 3
    assert s["expected_tool_count"] == EXPECTED_REDMINE_TOOL_COUNT
    assert s["all_ok"] is False


def test_pm_agent_down_flags_not_ok_but_no_gate():
    s = build_redmine_status(
        key_present=True, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT,
        pm_agent_ok=False, gate_url=None,
    )
    assert s["pm_agent_ok"] is False
    assert s["all_ok"] is False
    assert s["gate_url"] is None  # pm-agent is not key-fixable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_redmine_status.py -v`
Expected: FAIL — `meeting.api.redmine` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# meeting/api/redmine.py
"""GET /api/redmine/status — post-login probe for the FE banner + gate.

Reports, for the current user: whether AgentBase holds their Redmine key, whether
the Redmine MCP tool surface is registered as expected, and whether pm-agent is
configured. When the key is missing, includes the AgentBase consent gate_url so
the FE can open the redirect flow. pm-agent keeps its OWN creds — reported, not
key-gated. The status-shaping logic is pure (build_redmine_status) for offline
unit tests; the route wires deps to it.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends

from meeting.auth import get_current_user
from meeting.db.models import User
from meeting.services import tools as ts
from meeting.services.identity_client import get_identity_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/redmine", tags=["redmine"])

# The deployed Redmine MCP server exposes ~15 tools (README documented 5; live
# surface is larger). A mismatch means discovery failed or the surface drifted.
EXPECTED_REDMINE_TOOL_COUNT = 15

# Names that are NOT Redmine MCP tools (so we can count the redmine surface).
# Redmine tools are dynamically registered; count those present in TOOLS that
# came from the MCP server. We approximate via a known prefix/registry snapshot:
_REDMINE_TOOL_HINTS = ("redmine", "issue", "overdue", "workload", "field_metadata", "project")


def count_registered_redmine_tools() -> int:
    """How many Redmine MCP tools are currently registered in TOOLS."""
    n = 0
    for name in ts.TOOLS:
        low = name.lower()
        if any(h in low for h in _REDMINE_TOOL_HINTS):
            n += 1
    return n


def build_redmine_status(
    *,
    key_present: bool,
    registered_tool_count: int,
    pm_agent_ok: bool,
    gate_url: Optional[str],
) -> dict:
    tools_ok = key_present and registered_tool_count == EXPECTED_REDMINE_TOOL_COUNT
    status = {
        "redmine_key_present": key_present,
        "redmine_tools_ok": tools_ok,
        "registered_tool_count": registered_tool_count,
        "expected_tool_count": EXPECTED_REDMINE_TOOL_COUNT,
        "pm_agent_ok": pm_agent_ok,
        "gate_url": gate_url if not key_present else None,
    }
    status["all_ok"] = tools_ok and pm_agent_ok
    return status


def _pm_agent_configured() -> bool:
    # v1: configuration presence, NOT a live A2A handshake (see plan scope note).
    return bool(os.getenv("PM_AGENT_URL", "") and os.getenv("TOKEN_AUTHEN_PM_AGENT", ""))


@router.get("/status")
async def redmine_status(user: User = Depends(get_current_user)) -> dict:
    oid = user.ms_oid
    key_present = False
    gate_url = None
    if oid:
        try:
            result = await get_identity_client().request_user_key(oid)
            key_present = bool(result.apikey)
            gate_url = result.authorization_url
        except Exception as e:  # AgentBase unreachable → unknown, fail soft
            logger.warning("redmine_status: identity probe failed: %s", e)
    return build_redmine_status(
        key_present=key_present,
        registered_tool_count=count_registered_redmine_tools(),
        pm_agent_ok=_pm_agent_configured(),
        gate_url=gate_url,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_redmine_status.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Register the router in `meeting/app.py`**

After the existing `app.include_router(voiceprints_router)` block (~line 528), add:

```python
    from meeting.api.redmine import router as redmine_router
    app.include_router(redmine_router)
```

(Match the existing import/registration style in that block — some routers are imported at top; if so, add `from meeting.api.redmine import router as redmine_router` alongside the others and call `app.include_router(redmine_router)`.)

- [ ] **Step 6: Run the full suite to confirm no regression**

Run: `… venv/bin/pytest tests/meeting -q`
Expected: PASS — baseline 336 + new tests, 0 failures.

- [ ] **Step 7: Commit**

```bash
git add meeting/api/redmine.py meeting/app.py tests/meeting/test_redmine_status.py
git commit -m "feat(redmine): GET /api/redmine/status probe (pure builder + thin route)"
```

---

## Phase 4 — Bootstrap script

### Task 7: One-shot provisioning script + env docs

**Files:**
- Create: `scripts/bootstrap_redmine_identity.py`
- Modify: `.env.example`
- Test: `tests/meeting/test_identity_client.py` (add a payload-builder test)

- [ ] **Step 1: Write the failing test** (append to `test_identity_client.py`)

```python
def test_bootstrap_payloads():
    from scripts.bootstrap_redmine_identity import identity_payload, provider_payload
    assert provider_payload("redmine") == {"name": "redmine"}
    p = identity_payload("mee", ["https://mee.example/redmine-callback"])
    assert p["name"] == "mee"
    assert p["allowedReturnUrls"] == ["https://mee.example/redmine-callback"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -k bootstrap -v`
Expected: FAIL — module `scripts.bootstrap_redmine_identity` not found.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/bootstrap_redmine_identity.py
"""One-shot: provision the `mee` agent identity + `redmine` delegated provider
on AgentBase Identity. Idempotent (409 Conflict → treated as already-exists).

Run ONCE per environment after setting GREENNODE_CLIENT_ID/SECRET and
AGENTBASE_REDMINE_RETURN_URL in .env:

    venv/bin/python scripts/bootstrap_redmine_identity.py

Pure payload builders are unit-tested; the network calls reuse the proven IAM
token flow from meeting.memory_client._get_token.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

from meeting.memory_client import _get_token
from meeting.services.identity_client import ALLOWED_IDENTITY_HOST, DEFAULT_IDENTITY_BASE


def provider_payload(name: str) -> dict:
    return {"name": name}


def identity_payload(name: str, allowed_return_urls: list[str]) -> dict:
    return {"name": name, "allowedReturnUrls": allowed_return_urls}


def _post(base: str, path: str, body: dict, token: str) -> tuple[int, str]:
    url = f"{base.rstrip('/')}{path}"
    if ALLOWED_IDENTITY_HOST not in url:
        raise ValueError(f"refusing non-allowlisted URL: {url}")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    load_dotenv(override=True, interpolate=False)
    base = os.getenv("AGENTBASE_IDENTITY_URL", DEFAULT_IDENTITY_BASE)
    identity = os.getenv("AGENTBASE_AGENT_IDENTITY", "mee")
    provider = os.getenv("REDMINE_DELEGATED_PROVIDER", "redmine")
    return_url = os.getenv("AGENTBASE_REDMINE_RETURN_URL", "")
    if not return_url:
        print("ERROR: set AGENTBASE_REDMINE_RETURN_URL in .env first", file=sys.stderr)
        return 2

    token = _get_token()

    st, body = _post(base, "/agent-identities", identity_payload(identity, [return_url]), token)
    print(f"create identity {identity!r}: HTTP {st} {body[:300]}")
    if st not in (200, 201, 409):
        return 1

    st, body = _post(base, "/outbound-auth/delegated-api-key-providers", provider_payload(provider), token)
    print(f"create provider {provider!r}: HTTP {st} {body[:300]}")
    if st not in (200, 201, 409):
        return 1

    print("Bootstrap complete (409 = already existed, which is fine).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `… venv/bin/pytest tests/meeting/test_identity_client.py -k bootstrap -v`
Expected: PASS.

- [ ] **Step 5: Document env vars in `.env.example`**

Append:

```bash
# ─── Per-user Redmine key via AgentBase Identity ───────────────────────
# AgentBase Identity service base (default is correct for prod).
AGENTBASE_IDENTITY_URL=https://agentbase.api.vngcloud.vn/identity/api/v1
# Dedicated agent identity created by scripts/bootstrap_redmine_identity.py.
AGENTBASE_AGENT_IDENTITY=mee
# Delegated provider name (created once by the bootstrap script).
REDMINE_DELEGATED_PROVIDER=redmine
# Mee's consent callback — MUST be whitelisted in the agent identity's
# allowedReturnUrls (the bootstrap script does this). Set to your deployed
# Mee origin + /redmine-callback, e.g. http://localhost:8001/redmine-callback
AGENTBASE_REDMINE_RETURN_URL=
# Dev-only: when "1"/"true", fall back to the shared REDMINE_API_KEY for users
# without an OID (mock/local). Leave unset in production.
REDMINE_DEV_FALLBACK=
```

- [ ] **Step 6: Commit**

```bash
git add scripts/bootstrap_redmine_identity.py .env.example tests/meeting/test_identity_client.py
git commit -m "feat(redmine): bootstrap script for mee identity + redmine delegated provider"
```

- [ ] **Step 7: Run the bootstrap once (manual, requires real creds + decided return URL)**

```bash
# Set AGENTBASE_REDMINE_RETURN_URL in .env first, then:
venv/bin/python scripts/bootstrap_redmine_identity.py
```
Expected: two HTTP 200/201/409 lines, "Bootstrap complete". Then **confirm** with a read-only re-probe (the same list call used during planning) that `redmine` now appears under delegated providers and `mee` under agent identities with the return URL in `allowedReturnUrls`.

---

## Phase 5 — Frontend (manual verification — no FE test runner)

### Task 8: Add `redmine.status()` to the API client

**Files:**
- Modify: `meeting_frontend_react/src/api/client.ts`

- [ ] **Step 1: Add the endpoint** — inside the `api` object, after the `auth` block, add:

```typescript
  // ─── Redmine per-user key status ───────────────────────────────────
  redmine: {
    /** Post-login probe: key present? tools ok? pm-agent ok? + consent gate_url. */
    status: () =>
      http<{
        redmine_key_present: boolean;
        redmine_tools_ok: boolean;
        registered_tool_count: number;
        expected_tool_count: number;
        pm_agent_ok: boolean;
        gate_url: string | null;
        all_ok: boolean;
      }>("GET", "/api/redmine/status"),
  },
```

- [ ] **Step 2: Type-check**

Run: `cd meeting_frontend_react && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add meeting_frontend_react/src/api/client.ts
git commit -m "feat(fe): redmine.status() API client endpoint"
```

---

### Task 9: `RedmineStatusBanner` component + i18n

**Files:**
- Create: `meeting_frontend_react/src/components/RedmineStatusBanner.tsx`
- Modify: `meeting_frontend_react/src/i18n.ts`

- [ ] **Step 1: Add i18n strings** — add to BOTH `vi` and `en` in `src/i18n.ts`:

```typescript
    // redmine status banner
    "redmine.bannerKeyMissing": /* vi */ "Chưa có Redmine key của bạn — một số tính năng dự án sẽ không hoạt động.",
    "redmine.bannerToolsDown": /* vi */ "Không kết nối được công cụ Redmine.",
    "redmine.bannerPmDown": /* vi */ "Không kết nối được pm-agent.",
    "redmine.enterKey": /* vi */ "Nhập Redmine key",
```
English values (in the `en` block):
```typescript
    "redmine.bannerKeyMissing": "Your Redmine key is missing — some project features won't work.",
    "redmine.bannerToolsDown": "Redmine tools are unreachable.",
    "redmine.bannerPmDown": "pm-agent is unreachable.",
    "redmine.enterKey": "Enter Redmine key",
```

- [ ] **Step 2: Create the component**

```tsx
// meeting_frontend_react/src/components/RedmineStatusBanner.tsx
import { useApp } from "../store/AppContext";

export interface RedmineStatus {
  redmine_key_present: boolean;
  redmine_tools_ok: boolean;
  pm_agent_ok: boolean;
  gate_url: string | null;
  all_ok: boolean;
}

interface RedmineStatusBannerProps {
  status: RedmineStatus;
}

/**
 * Red warning banner shown when the user's Redmine key is missing or a service
 * (Redmine tools / pm-agent) is unreachable. A missing key offers a button that
 * redirects to the AgentBase consent gate; on return the app re-probes status.
 */
export function RedmineStatusBanner({ status }: RedmineStatusBannerProps) {
  const { t } = useApp();
  if (status.all_ok) return null;

  const messages: string[] = [];
  if (!status.redmine_key_present) messages.push(t("redmine.bannerKeyMissing"));
  else if (!status.redmine_tools_ok) messages.push(t("redmine.bannerToolsDown"));
  if (!status.pm_agent_ok) messages.push(t("redmine.bannerPmDown"));

  const openGate = () => {
    if (status.gate_url) window.location.href = status.gate_url;
  };

  return (
    <div className="redmine-banner" role="alert" style={{ color: "#c0271e" }}>
      <span className="redmine-banner-text">{messages.join(" ")}</span>
      {!status.redmine_key_present && status.gate_url && (
        <button type="button" className="redmine-banner-action" onClick={openGate}>
          {t("redmine.enterKey")}
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd meeting_frontend_react && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add meeting_frontend_react/src/components/RedmineStatusBanner.tsx meeting_frontend_react/src/i18n.ts
git commit -m "feat(fe): RedmineStatusBanner (red) + VI/EN strings"
```

---

### Task 10: Wire status probe + banner + gate return into the app

**Files:**
- Modify: `meeting_frontend_react/src/App.tsx`

- [ ] **Step 1: Fetch status in `MainApp` and render the banner.** Replace the `MainApp` function body so it probes `/api/redmine/status` on mount and after returning from the consent gate, and renders the banner above the workspace:

```tsx
function MainApp({ user }: { user: Me }) {
  const [redmine, setRedmine] = useState<RedmineStatus | null>(null);

  const probeRedmine = async () => {
    try {
      setRedmine(await api.redmine.status());
    } catch (e) {
      console.warn("[redmine/status] probe failed:", e);
      setRedmine(null);
    }
  };

  useEffect(() => {
    document.body.style.display = "";
    document.body.style.height = "";
    document.body.style.overflow = "";
    probeRedmine();
    // Re-probe when the tab regains focus (e.g. returning from the consent gate).
    const onFocus = () => probeRedmine();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  return (
    <AppProvider>
      <Sidebar />
      <div className="app">
        <Topbar user={user} />
        {redmine && <RedmineStatusBanner status={redmine} />}
        <MeetingControl />
        <Workspace />
      </div>
    </AppProvider>
  );
}
```

- [ ] **Step 2: Add imports** at the top of `App.tsx`:

```tsx
import { RedmineStatusBanner, type RedmineStatus } from "./components/RedmineStatusBanner";
```

- [ ] **Step 3: Type-check + build**

Run: `cd meeting_frontend_react && npx tsc --noEmit && npm run build`
Expected: clean build into `dist/`.

- [ ] **Step 4: Commit**

```bash
git add meeting_frontend_react/src/App.tsx
git commit -m "feat(fe): probe redmine status post-login, show banner + re-probe on focus"
```

---

## Phase 6 — Verification & wrap-up

### Task 11: Full backend suite + manual smoke

- [ ] **Step 1: Full backend suite**

Run: `… venv/bin/pytest tests/meeting -q`
Expected: 336 baseline + new tests pass, **0 failures**.

- [ ] **Step 2: Manual smoke (requires bootstrap from Task 7 done + a real O365 login)**

1. `venv/bin/python run_meeting.py` and `cd meeting_frontend_react && npm run dev`.
2. Log in as a real O365 user that has **no** Redmine key yet → red banner appears with "Nhập Redmine key"; clicking redirects to the AgentBase consent page.
3. Complete consent (enter the Redmine key there) → returning to the tab re-probes → banner clears.
4. In chat, exercise a Redmine read tool → it returns data scoped to *that user's* projects (confirms per-call Bearer).
5. Log in as a user whose key is absent and `REDMINE_DEV_FALLBACK` unset → a Redmine tool call yields the agent surfacing a clean "missing key" message (the turn finishes; no retry loop), confirming the `redmine_key_missing` + `f67e318` guard pairing.

- [ ] **Step 3: Confirm the raw REST field names** observed in the first live `request-key` round-trip match what `parse_request_key_response` tolerates. If a new spelling appears, add it to the `_first(...)` lists and re-run Task 2's tests. (This is the one contract detail that could not be live-probed before implementation.)

---

## Scope notes / deliberate v1 limitations

- **`pm_agent_ok` is configuration-presence, not a live A2A handshake.** A real ping needs the user's Graph bearer and an agent-card round-trip; deferred. The banner still warns when pm-agent config is absent. Flagged so it can be upgraded later.
- **Tool-count check is a registry snapshot**, not a live `users/current.json` ping — cheap and network-free, mirrors the spec's "tool count == expected" option. A mismatch is reported (`registered_tool_count` vs `expected_tool_count`).
- **Global `REDMINE_API_KEY` is retired as the runtime source of truth**; it survives only as the opt-in `REDMINE_DEV_FALLBACK` path for OID-less dev/mock users.
- **No DB migration** — `ms_oid` already exists; nothing schema-side changes.

## Self-review (done)

- **Spec coverage:** identity client (Tasks 1–3) ✓; per-user key at `_proxy` + structured `redmine_key_missing` (Tasks 4–5) ✓; `GET /api/redmine/status` with per-service flags + `gate_url` (Task 6) ✓; gate completion via FE re-probe (Task 10) ✓; red banner naming the failing service (Task 9) ✓; VI/EN i18n (Task 9) ✓; bootstrap of provider + identity + returnUrl whitelist (Task 7) ✓; fail-closed error handling, no secret in logs (Tasks 2, 5, 6) ✓; TDD offline tests (every backend task) ✓.
- **Type consistency:** `RequestKeyResult(apikey, authorization_url, status)`, `get_user_key`/`get_cached_user_key`, `resolve_redmine_key(user_id, session)`, `call_tool(..., api_key=)`, `build_redmine_status(key_present=, registered_tool_count=, pm_agent_ok=, gate_url=)`, `RedmineStatus` FE type — all consistent across tasks.
- **Placeholders:** none — every code step has full content.
