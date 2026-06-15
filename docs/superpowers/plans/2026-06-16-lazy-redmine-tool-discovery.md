# Lazy Per-User Redmine Tool Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register Redmine MCP tools by discovering their schemas with a *per-user* AgentBase Identity key on the first authenticated request, instead of a user-less startup call that needs a shared/env key.

**Architecture:** Tool *schemas* (names + input shapes) are user-independent, so we discover them once using whichever authenticated user's delegated Redmine key is available first, then cache + register them process-wide. Startup becomes cache-only (no network, no key). This removes the only remaining dependency on a global `REDMINE_API_KEY` — the sole Redmine auth that ever happens is the per-user key stored in the `mee` AgentBase Identity delegated `redmine` provider.

**Tech Stack:** FastAPI, LangGraph, AgentBase Identity (delegated API-key provider), MCP streamable-http client, pytest (`asyncio_mode=auto`).

---

## Background / Why (read before coding)

The per-user key feature (already shipped on `feat/build-agentbase`) works for tool **calls**: `meeting/services/tools/redmine.py::_proxy._exec` resolves the current user's key via `resolve_redmine_key(user_id, session)` → `get_cached_user_key(oid)` (outbound to AgentBase Identity) and passes it as `api_key=` to `RedmineMcpClient.call_tool`. ✅

The broken part is tool **registration**. At startup, `meeting/app.py` lifespan calls `load_and_register_redmine_tools()`, which does a live `list_tools()` against the MCP server. That call runs **before any user logs in**, so it has no per-user key and falls back to the env `REDMINE_API_KEY` (empty on the deployed runtime) → the MCP `/mcp` session POST returns **401** → discovery returns `[]` → **0 tools registered** → `redmine_tools_ok: false` forever, and the working per-user call path has nothing to call.

Confirmed in deployed logs:
- `POST https://mcp-redmine.vngcloud.vn/mcp → 401 Unauthorized` (discovery)
- `POST .../delegated-api-key-providers/redmine/agent-identities/mee/api-key → 200 OK` (per-user fetch works)

**Decision (user, 2026-06-16):** discovery must use the *same delegated `redmine` provider* (per-user key). Therefore we do **not** discover at startup — we lazily discover/register on the first authenticated user's request, using that user's key. No static/service key, no baked cache, no env key.

## Current behavior (exact references)

- `meeting/services/tools/redmine.py`
  - `fetch_redmine_tool_schemas()` (≈141-149): `client = get_redmine_mcp_client(); async with client._session() as session: result = await session.list_tools()`. Uses `_session()` with **no** key → env key.
  - `load_and_register_redmine_tools(*, force=False)` (≈152-167): `url = client._url`; `schemas = None if force else _load_cache(url)`; if `None` → `fetch_redmine_tool_schemas()` (network, env key) → `_save_cache`; then `register_redmine_tools(schemas)`. On exception logs `[redmine-mcp] tool discovery failed (skipping)` and returns `[]`.
  - `register_redmine_tools(schemas)` (≈86-105): pure; builds a `_proxy(name)` executor per schema and registers via `tool(...)`. **No change needed.**
  - `resolve_redmine_key(user_id, session)` (≈63-72): dev fallback (`REDMINE_DEV_FALLBACK` + env `REDMINE_API_KEY`) → `_oid_for_user` → `get_cached_user_key(oid)`. **Reuse as-is.**
  - `_load_cache` / `_save_cache` / `_cache_path` (≈108+): cache keyed by server URL; path from `MCP_REDMINE_TOOLS_CACHE` env or `.mcp_redmine_tools_cache.json`.
- `meeting/services/redmine_mcp_client.py`
  - `_session(api_key: Optional[str] = None)` (≈74-90): already passes `_auth_headers(api_key)` (per-call key → else env key). **Supports a key already — no change.**
  - `get_redmine_mcp_client()` (≈106-114): lazy env singleton (`MCP_REDMINE_URL`, `REDMINE_API_KEY`).
- `meeting/app.py` (≈499-500): lifespan `from meeting.services import load_and_register_redmine_tools; await load_and_register_redmine_tools()`.
- `meeting/api/redmine.py`: `count_registered_redmine_tools()` powers `registered_tool_count` in `GET /api/redmine/status`.

## Test/run notes

- venv is `venv/` (NOT `.venv/`). `asyncio_mode=auto` (async tests need no marker).
- Offline test command (mirror existing): `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q`
- Existing tests to mirror for monkeypatch patterns: `tests/meeting/test_tools_redmine.py` (esp. `test_registered_executor_forwards_resolved_user_key`, `test_resolve_redmine_key_dev_fallback`).
- DB has Alembic drift — run backend WITHOUT `alembic upgrade head`. No DB migration in this plan.

---

## File Structure

- Modify: `meeting/services/tools/redmine.py` — add key-aware discovery + lazy `ensure_redmine_tools_registered`; make startup cache-only.
- Modify: `meeting/services/__init__.py` and `meeting/services/tools/__init__.py` — export `ensure_redmine_tools_registered`.
- Modify: `meeting/graphs/chat_graph/runner.py` — call the lazy hook before `graph.ainvoke` (see Task 4; located/verified).
- Modify: `meeting/api/redmine.py` — optionally trigger `ensure_redmine_tools_registered` in the status route so the banner reflects reality once a key exists (verify it can get a DB session + the internal user id).
- Test: `tests/meeting/test_tools_redmine.py` (extend), and a new `tests/meeting/test_redmine_lazy_discovery.py`.

---

## Task 1: Key-aware schema fetch

**Files:**
- Modify: `meeting/services/tools/redmine.py` (`fetch_redmine_tool_schemas`)
- Test: `tests/meeting/test_redmine_lazy_discovery.py`

- [ ] **Step 1: Write the failing test** — `fetch_redmine_tool_schemas(api_key="k")` opens the MCP session with that key.

```python
# tests/meeting/test_redmine_lazy_discovery.py
import meeting.services.tools as tools
from meeting.services.tools import redmine

async def test_fetch_schemas_passes_api_key(monkeypatch):
    captured = {}

    class _FakeSession:
        async def list_tools(self):
            class _T:  # minimal duck-typed tool
                def __init__(self, n): self.name = n; self.description = ""; self.inputSchema = {}
            class _R: tools = [_T("get_redmine_projects")]
            return _R()

    class _FakeClient:
        def _session(self, api_key=None):
            captured["api_key"] = api_key
            class _Ctx:
                async def __aenter__(self_): return _FakeSession()
                async def __aexit__(self_, *a): return False
            return _Ctx()

    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _FakeClient())
    schemas = await redmine.fetch_redmine_tool_schemas(api_key="user-key-1")
    assert captured["api_key"] == "user-key-1"
    assert schemas == [{"name": "get_redmine_projects", "description": "", "inputSchema": {}}]
```

- [ ] **Step 2: Run it, verify it fails** — `venv/bin/pytest tests/meeting/test_redmine_lazy_discovery.py -q` (TypeError: unexpected kwarg `api_key`).

- [ ] **Step 3: Implement** — add the param and forward it:

```python
async def fetch_redmine_tool_schemas(api_key: Optional[str] = None) -> list[dict]:
    """Live-fetch tool schemas from the MCP server, authenticated with `api_key`."""
    client = get_redmine_mcp_client()
    async with client._session(api_key=api_key) as session:
        result = await session.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema or {}}
            for t in result.tools
        ]
```

- [ ] **Step 4: Run test → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(redmine): key-aware tool-schema discovery"`

## Task 2: Startup becomes cache-only (no network/key)

**Files:**
- Modify: `meeting/services/tools/redmine.py` (`load_and_register_redmine_tools`)
- Test: `tests/meeting/test_redmine_lazy_discovery.py`

- [ ] **Step 1: Write failing tests** — startup never calls the network; registers from cache if present, else registers nothing.

```python
async def test_startup_registers_from_cache_without_network(monkeypatch):
    monkeypatch.setattr(redmine, "get_redmine_mcp_client",
                        lambda: type("C", (), {"_url": "https://mcp/x/mcp"})())
    monkeypatch.setattr(redmine, "_load_cache", lambda url: [
        {"name": "get_redmine_projects", "description": "", "inputSchema": {}}])
    def _boom(*a, **k): raise AssertionError("no network at startup")
    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _boom)
    names = await redmine.load_and_register_redmine_tools()
    assert names == ["get_redmine_projects"]
    tools.TOOLS.pop("get_redmine_projects", None)

async def test_startup_no_cache_registers_nothing(monkeypatch):
    monkeypatch.setattr(redmine, "get_redmine_mcp_client",
                        lambda: type("C", (), {"_url": "https://mcp/x/mcp"})())
    monkeypatch.setattr(redmine, "_load_cache", lambda url: None)
    def _boom(*a, **k): raise AssertionError("no network at startup")
    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _boom)
    names = await redmine.load_and_register_redmine_tools()
    assert names == []
```

- [ ] **Step 2: Run → fail** (current code calls `fetch_redmine_tool_schemas` on cache miss).

- [ ] **Step 3: Implement** — drop the network branch from startup:

```python
async def load_and_register_redmine_tools(*, force: bool = False) -> list[str]:
    """Startup registration: cache-only, never hits the network.

    Discovery now happens lazily per-user (ensure_redmine_tools_registered) because
    the MCP server authenticates with the caller's per-user Redmine key, which does
    not exist at startup. If a schema cache from a prior run is present we register
    from it; otherwise we register nothing and wait for the first authenticated user.
    """
    url = get_redmine_mcp_client()._url
    schemas = None if force else _load_cache(url)
    if schemas is None:
        logger.info("[redmine-mcp] no schema cache; deferring discovery to first authenticated user")
        return []
    return register_redmine_tools(schemas)
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(redmine): startup tool registration is cache-only (lazy per-user discovery)"`

## Task 3: Lazy per-user discovery entrypoint

**Files:**
- Modify: `meeting/services/tools/redmine.py` (add `ensure_redmine_tools_registered`)
- Test: `tests/meeting/test_redmine_lazy_discovery.py`

Design: idempotent. If Redmine tools already registered → no-op. Else resolve the user's key; if none → no-op (user will get the gate). Else discover-with-key → register + cache.

- [ ] **Step 1: Write failing tests.**

```python
async def test_ensure_noop_when_already_registered(monkeypatch):
    redmine.register_redmine_tools([{"name": "get_overdue_issues", "description": "", "inputSchema": {}}])
    def _boom(*a, **k): raise AssertionError("should not resolve/fetch when already registered")
    monkeypatch.setattr(redmine, "resolve_redmine_key", _boom)
    await redmine.ensure_redmine_tools_registered(user_id="u1", session=None)
    tools.TOOLS.pop("get_overdue_issues", None)

async def test_ensure_noop_when_no_key(monkeypatch):
    async def _no_key(user_id, session): return None
    monkeypatch.setattr(redmine, "resolve_redmine_key", _no_key)
    def _boom(*a, **k): raise AssertionError("must not fetch without a key")
    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _boom)
    await redmine.ensure_redmine_tools_registered(user_id="u1", session=None)
    assert redmine.count_registered() == 0  # see helper note below

async def test_ensure_discovers_with_user_key_and_caches(monkeypatch):
    async def _key(user_id, session): return "user-key-9"
    monkeypatch.setattr(redmine, "resolve_redmine_key", _key)
    captured = {}
    async def _fetch(api_key=None):
        captured["api_key"] = api_key
        return [{"name": "list_redmine_issue", "description": "", "inputSchema": {}}]
    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _fetch)
    saved = {}
    monkeypatch.setattr(redmine, "_save_cache", lambda url, schemas: saved.update(url=url, schemas=schemas))
    monkeypatch.setattr(redmine, "get_redmine_mcp_client",
                        lambda: type("C", (), {"_url": "https://mcp/x/mcp"})())
    await redmine.ensure_redmine_tools_registered(user_id="u1", session=None)
    assert captured["api_key"] == "user-key-9"
    assert "list_redmine_issue" in tools.TOOLS
    assert saved["schemas"][0]["name"] == "list_redmine_issue"
    tools.TOOLS.pop("list_redmine_issue", None)
```

> Helper note: tests reference `redmine.count_registered()` — either add a tiny module helper `def count_registered() -> int` that reuses the same hint logic as `meeting/api/redmine.py::count_registered_redmine_tools`, OR assert directly on `tools.TOOLS`. Keep one source of truth for the hint list; if you add a helper, have `api/redmine.py` import it to avoid duplication (DRY).

- [ ] **Step 2: Run → fail** (`ensure_redmine_tools_registered` undefined).

- [ ] **Step 3: Implement.** Guard concurrent first-requests with an `asyncio.Lock` so two simultaneous turns don't double-discover.

```python
import asyncio
_discovery_lock = asyncio.Lock()

def _redmine_tools_registered() -> bool:
    # reuse the same hint set as api/redmine.count_registered_redmine_tools
    return count_registered() > 0

async def ensure_redmine_tools_registered(user_id, session) -> list[str]:
    """Lazily discover+register Redmine tools using the current user's key.

    Idempotent and best-effort: no-op if already registered or if the user has no
    key yet (they'll see the consent gate). Never raises into the request path.
    """
    if _redmine_tools_registered():
        return []
    key = await resolve_redmine_key(user_id, session)
    if not key:
        return []
    async with _discovery_lock:
        if _redmine_tools_registered():  # re-check after acquiring the lock
            return []
        try:
            schemas = await fetch_redmine_tool_schemas(api_key=key)
        except Exception as e:
            logger.warning("[redmine-mcp] lazy discovery failed (skipping): %s", e)
            return []
        _save_cache(get_redmine_mcp_client()._url, schemas)
        return register_redmine_tools(schemas)
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Export it** — add `ensure_redmine_tools_registered` to `__all__` and imports in `meeting/services/tools/__init__.py` and `meeting/services/__init__.py` (mirror how `load_and_register_redmine_tools` is exported).
- [ ] **Step 6: Commit** — `git commit -m "feat(redmine): lazy per-user tool discovery entrypoint"`

## Task 4: Hook lazy discovery into the chat request path

**LOCATED (verified 2026-06-16 on `feat/build-agentbase`) — no need to re-search:**
- The exact hook is `meeting/graphs/chat_graph/runner.py`. Both `run_chat_turn(*, session_id, user_id, user_message, meeting_id, session, checkpointer, ...)` (≈92-121) and `resume_chat_turn(...)` (≈156+) take **`user_id` and `session` as parameters** and call `graph.ainvoke` (≈120). Add the hook **immediately before `graph.ainvoke`** in BOTH.
- Why this works same-turn: the graph is rebuilt every turn (`runner.py:115 graph = build_chat_graph(session, checkpointer)`), AND the agent enumerates tools dynamically per invocation via `_openai_tools()` → `tools.list_tools()` (`meeting/graphs/chat_graph/agent.py:79-84`). So tools registered before `ainvoke` are visible to the LLM that same turn — no graph-build-time snapshot to worry about.
- It must be best-effort (never raise into the turn) — `ensure_redmine_tools_registered` already swallows its own errors (Task 3), so a bare `await` is safe.

**Files:**
- Modify: `meeting/graphs/chat_graph/runner.py` — `run_chat_turn` and `resume_chat_turn`.
- Test: extend `tests/meeting/test_redmine_lazy_discovery.py` (or the chat-graph runner tests) to assert `ensure_redmine_tools_registered` is awaited with the turn's `user_id`/`session` before `ainvoke`.

- [ ] **Step 1: Write the failing test** — invoking `run_chat_turn` calls `ensure_redmine_tools_registered(user_id, session)` once (monkeypatch the symbol imported into `runner`, and monkeypatch `build_chat_graph` to a fake graph whose `ainvoke`/`aget_state` are stubbed so no DB is needed).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — at the top of `run_chat_turn` and `resume_chat_turn`, just before `graph.ainvoke`:

```python
from meeting.services import ensure_redmine_tools_registered  # module-top import
...
await ensure_redmine_tools_registered(user_id, session)  # best-effort; never raises
result = await graph.ainvoke(initial_state, config=config)
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat(chat): lazily register Redmine tools on first authenticated turn"`

## Task 5 (optional but recommended): Status route triggers discovery

So `GET /api/redmine/status` reports `registered_tool_count: 15` once the user has a key, even before their first chat turn (otherwise the banner says tools-down until they chat).

**Files:**
- Modify: `meeting/api/redmine.py`
- Test: `tests/meeting/test_redmine_status.py`

- [ ] **Step 1: Write failing test** — when `request_user_key` returns a key and discovery is stubbed to register N tools, the status route reports them. Verify the route can obtain a DB session + the internal `user_id` needed by `resolve_redmine_key`/`_oid_for_user`. If the route can't cheaply get a session, SKIP this task (the chat-path hook in Task 4 is sufficient) and note that in the commit.
- [ ] **Step 2-4:** implement `await ensure_redmine_tools_registered(...)` before `count_registered_redmine_tools()` in the route; run tests.
- [ ] **Step 5: Commit** — `git commit -m "feat(redmine): status route lazily registers tools when a key exists"`

## Task 6: Full regression + cleanup

- [ ] Run the whole offline suite: `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q` — expect all prior tests green plus the new ones.
- [ ] Verify no remaining code path requires env `REDMINE_API_KEY` for discovery (it remains only as the dev-fallback inside `resolve_redmine_key` behind `REDMINE_DEV_FALLBACK`). Confirm `get_redmine_mcp_client`'s `api_key=os.getenv("REDMINE_API_KEY","")` is now only a last-resort `_session()` default that lazy discovery no longer relies on.
- [ ] Final code review (superpowers:requesting-code-review) then superpowers:finishing-a-development-branch.

## Done criteria
- Deployed runtime with **no** `REDMINE_API_KEY` env set: app boots, `registered_tool_count` starts at 0, and flips to 15 after the first user with a stored key chats (or loads status, if Task 5 done). `redmine_tools_ok: true`. The MCP `/mcp` 401 no longer appears in startup logs.

---

## Kickoff prompt for the fresh session

> Continue on branch `feat/build-agentbase` in the Mee meeting agent. Implement the plan at `docs/superpowers/plans/2026-06-16-lazy-redmine-tool-discovery.md` via superpowers:subagent-driven-development. Context: the per-user Redmine key feature works for tool *calls* but tool *registration* fails because startup discovery (`list_tools`) runs with no user → falls back to an empty env `REDMINE_API_KEY` → 401 → 0 tools. Fix = lazy per-user discovery (discover with the first authenticated user's key, cache the schemas, register process-wide). Do NOT reintroduce a shared/env/service key and do NOT bake a schema cache into the image — the user explicitly chose the delegated per-user provider as the discovery key source. venv is `venv/`; tests are offline with `asyncio_mode=auto` (baseline green): `DATABASE_URL=postgresql://u:p@localhost:5432/db DATABASE_URL_SYNC=postgresql://u:p@localhost:5432/db venv/bin/pytest tests/meeting -q`. Run backend WITHOUT `alembic upgrade head` (DB drift). The Task 4 hook point is already located: `meeting/graphs/chat_graph/runner.py` (`run_chat_turn` + `resume_chat_turn`, before `graph.ainvoke`).
