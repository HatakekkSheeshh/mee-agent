"""Lazy per-user Redmine tool discovery.

Discovery now uses the FIRST authenticated user's per-user key (not a startup
env key): startup is cache-only, and `ensure_redmine_tools_registered` does the
network discovery lazily + idempotently. Offline — no MCP server, no DB.
"""
from __future__ import annotations

import src.services.tools as tools
from src.services.tools import redmine

_FAKE = [{"name": "get_redmine_projects", "description": "", "inputSchema": {}}]
_EXTRA_NAMES = ("get_overdue_issues", "list_redmine_issue")


def teardown_function():
    # Keep the global registry + lazy-registration bookkeeping clean between tests.
    for s in _FAKE:
        tools.TOOLS.pop(s["name"], None)
    for n in _EXTRA_NAMES:
        tools.TOOLS.pop(n, None)
    redmine._registered_names.clear()


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = ""
        self.inputSchema = {}


def _fake_client(*, tool_names=("get_redmine_projects",), capture=None,
                 url="https://mcp.example/mcp"):
    class _Session:
        async def list_tools(self_):
            class _R:
                tools = [_FakeTool(n) for n in tool_names]
            return _R()

    class _Client:
        _url = url

        def _session(self_, api_key=None):
            if capture is not None:
                capture["api_key"] = api_key

            class _Ctx:
                async def __aenter__(c):
                    return _Session()

                async def __aexit__(c, *a):
                    return False

            return _Ctx()

    return _Client()


# ── Task 1: key-aware discovery ────────────────────────────────────
async def test_fetch_schemas_passes_api_key(monkeypatch):
    cap = {}
    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _fake_client(capture=cap))
    schemas = await redmine.fetch_redmine_tool_schemas(api_key="user-key-1")
    assert cap["api_key"] == "user-key-1"
    assert schemas == [{"name": "get_redmine_projects", "description": "", "inputSchema": {}}]


# ── Task 2: startup is cache-only (never hits the network) ─────────
async def test_startup_registers_from_cache_without_network(monkeypatch):
    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _fake_client())
    monkeypatch.setattr(redmine, "_load_cache", lambda url: list(_FAKE))

    def _boom(*a, **k):
        raise AssertionError("startup must not hit the network")

    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _boom)
    names = await redmine.load_and_register_redmine_tools()
    assert names == ["get_redmine_projects"]
    assert "get_redmine_projects" in tools.TOOLS


async def test_startup_no_cache_registers_nothing(monkeypatch):
    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _fake_client())
    monkeypatch.setattr(redmine, "_load_cache", lambda url: None)

    def _boom(*a, **k):
        raise AssertionError("startup must not hit the network")

    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _boom)
    names = await redmine.load_and_register_redmine_tools()
    assert names == []
    assert not redmine._redmine_tools_registered()


# ── Task 3: lazy per-user discovery entrypoint ─────────────────────
async def test_ensure_noop_when_already_registered(monkeypatch):
    redmine.register_redmine_tools(
        [{"name": "get_overdue_issues", "description": "", "inputSchema": {}}]
    )

    def _boom(*a, **k):
        raise AssertionError("must not resolve a key when already registered")

    monkeypatch.setattr(redmine, "resolve_redmine_key", _boom)
    out = await redmine.ensure_redmine_tools_registered(user_id="u1", session=None)
    assert out == []


async def test_ensure_noop_when_no_key(monkeypatch):
    async def _no_key(user_id, session):
        return None

    monkeypatch.setattr(redmine, "resolve_redmine_key", _no_key)

    def _boom(*a, **k):
        raise AssertionError("must not fetch schemas without a key")

    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _boom)
    out = await redmine.ensure_redmine_tools_registered(user_id="u1", session=None)
    assert out == []
    assert not redmine._redmine_tools_registered()


async def test_ensure_discovers_with_user_key_and_caches(monkeypatch):
    async def _key(user_id, session):
        return "user-key-9"

    monkeypatch.setattr(redmine, "resolve_redmine_key", _key)

    cap = {}

    async def _fetch(api_key=None):
        cap["api_key"] = api_key
        return [{"name": "list_redmine_issue", "description": "", "inputSchema": {}}]

    monkeypatch.setattr(redmine, "fetch_redmine_tool_schemas", _fetch)

    saved = {}
    monkeypatch.setattr(
        redmine, "_save_cache",
        lambda url, schemas: saved.update(url=url, schemas=schemas),
    )
    monkeypatch.setattr(redmine, "get_redmine_mcp_client", lambda: _fake_client())

    out = await redmine.ensure_redmine_tools_registered(user_id="u1", session=None)
    assert cap["api_key"] == "user-key-9"
    assert out == ["list_redmine_issue"]
    assert "list_redmine_issue" in tools.TOOLS
    assert saved["schemas"][0]["name"] == "list_redmine_issue"
    assert redmine._redmine_tools_registered()
