"""Redmine MCP tools — DYNAMIC registration of the deployed server's tools.

The deployed server's tool surface evolves (the README documented 5; the live
server exposes ~15), so we DISCOVER tools at runtime via list_tools() rather
than hardcoding schemas. Each discovered tool is registered into the local
TOOLS registry with an executor that proxies to the MCP client; writes are
marked side_effect so the chat graph's HITL machinery gates them.

Discovery hits the network, so it CANNOT run at import time. Call
load_and_register_redmine_tools() from the app lifespan (best-effort). A disk
cache keyed by server URL lets later boots skip the network — mirrors
pm-agent's mcp_http_client tool cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from meeting.db.models import User
from meeting.services.identity_client import get_cached_user_key
from meeting.services.redmine_mcp_client import get_redmine_mcp_client
from meeting.services.tools._registry import TOOLS, tool

logger = logging.getLogger(__name__)

# Names registered via register_redmine_tools, so lazy discovery can detect a
# prior registration without re-hitting the network. The lock serializes the
# concurrent first-turn discoveries that would otherwise all fire at once.
_registered_names: set[str] = set()
_discovery_lock = asyncio.Lock()


def _redmine_tools_registered() -> bool:
    """True if any previously-registered Redmine tool is still in TOOLS."""
    return any(name in TOOLS for name in _registered_names)

# Tools whose execution mutates Redmine → MUST be HITL-gated (side_effect).
WRITE_TOOLS = frozenset({
    "create_redmine_issue",
    "update_redmine_issue",
    "bulk_update_issues",
})
# Conservative fallback: any tool whose name starts with a mutating verb is
# treated as a write even if not in WRITE_TOOLS, so a newly-added write tool is
# gated by default rather than silently executed.
_WRITE_PREFIXES = ("create", "update", "delete", "bulk", "remove", "close")


def is_write_tool(name: str) -> bool:
    """True if the tool mutates Redmine (explicit set OR mutating-verb prefix)."""
    if name in WRITE_TOOLS:
        return True
    return (name or "").lower().startswith(_WRITE_PREFIXES)


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


def register_redmine_tools(schemas: list[dict]) -> list[str]:
    """Register discovered MCP tool schemas into the local TOOLS registry.

    `schemas` = [{name, description, inputSchema}]. Returns registered names.
    Network-free (the caller supplies schemas) so tests pass a fake list.
    """
    registered: list[str] = []
    for s in schemas:
        name = s.get("name")
        if not name:
            continue
        tool(
            name=name,
            description=s.get("description", "") or "",
            side_effect=is_write_tool(name),
            schema=s.get("inputSchema") or {"type": "object", "properties": {}},
        )(_proxy(name))
        registered.append(name)
    _registered_names.update(registered)
    logger.info("[redmine-mcp] registered %d tool(s): %s", len(registered), registered)
    return registered


# ── disk cache (keyed by server URL) ───────────────────────────────
def _cache_path() -> Path:
    return Path(os.getenv("MCP_REDMINE_TOOLS_CACHE", ".mcp_redmine_tools_cache.json")).resolve()


def _load_cache(url: str) -> Optional[list[dict]]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("[redmine-mcp] failed reading tool cache %s: %s", path, e)
        return None
    entry = data.get(url)
    return entry if isinstance(entry, list) and entry else None


def _save_cache(url: str, schemas: list[dict]) -> None:
    path = _cache_path()
    try:
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:
                existing = {}
        existing[url] = schemas
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[redmine-mcp] failed saving tool cache %s: %s", path, e)


async def fetch_redmine_tool_schemas(api_key: Optional[str] = None) -> list[dict]:
    """Live-fetch tool schemas from the MCP server, authenticated with `api_key`."""
    client = get_redmine_mcp_client()
    async with client._session(api_key=api_key) as session:
        result = await session.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema or {}}
            for t in result.tools
        ]


async def load_and_register_redmine_tools(*, force: bool = False) -> list[str]:
    """Startup registration: cache-only, never hits the network.

    Discovery now happens lazily per-user (ensure_redmine_tools_registered),
    because the MCP server authenticates with the caller's per-user Redmine key,
    which does not exist at startup. If a prior run's disk cache is present we
    register from it; otherwise we register nothing and wait for the first
    authenticated user. `force` bypasses the cache (→ registers nothing here).
    """
    url = get_redmine_mcp_client()._url
    schemas = None if force else _load_cache(url)
    if schemas is None:
        logger.info(
            "[redmine-mcp] no schema cache; deferring discovery to first authenticated user"
        )
        return []
    return register_redmine_tools(schemas)


async def _discover_and_register(key: str) -> list[str]:
    """Discover schemas with `key` and register them, serialized by the lock."""
    async with _discovery_lock:
        if _redmine_tools_registered():  # re-check now that we hold the lock
            return []
        try:
            schemas = await fetch_redmine_tool_schemas(api_key=key)
        except Exception as e:
            logger.warning("[redmine-mcp] lazy discovery failed (skipping): %s", e)
            return []
        _save_cache(get_redmine_mcp_client()._url, schemas)
        return register_redmine_tools(schemas)


async def ensure_redmine_tools_registered(user_id, session) -> list[str]:
    """Lazily discover + register Redmine tools using the current user's key.

    Idempotent and best-effort: no-op if tools are already registered or if the
    user has no key yet (they'll see the consent gate). Never raises into the
    request path. This is how the user-independent tool *schemas* get discovered
    without a shared/env key — the first authenticated user's delegated key lists
    them, then they're cached + registered process-wide.
    """
    if _redmine_tools_registered():
        return []
    key = await resolve_redmine_key(user_id, session)
    if not key:
        return []
    return await _discover_and_register(key)


async def ensure_redmine_tools_with_key(key: Optional[str]) -> list[str]:
    """Variant for callers that already hold the user's key (e.g. the status
    route just fetched it) — skips the DB/oid round-trip. No-op if tools are
    already registered or no key is supplied."""
    if _redmine_tools_registered() or not key:
        return []
    return await _discover_and_register(key)
