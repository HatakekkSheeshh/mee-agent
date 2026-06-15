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

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from meeting.db.models import User
from meeting.services.identity_client import get_cached_user_key
from meeting.services.redmine_mcp_client import get_redmine_mcp_client
from meeting.services.tools._registry import tool

logger = logging.getLogger(__name__)

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


async def fetch_redmine_tool_schemas() -> list[dict]:
    """Live-fetch tool schemas from the MCP server (no cache)."""
    client = get_redmine_mcp_client()
    async with client._session() as session:
        result = await session.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema or {}}
            for t in result.tools
        ]


async def load_and_register_redmine_tools(*, force: bool = False) -> list[str]:
    """Resolve tool schemas (disk cache → live fetch) and register them.

    Best-effort: returns [] and logs on any failure so the app still boots when
    the Redmine MCP server is unreachable. Call from the FastAPI lifespan.
    """
    url = get_redmine_mcp_client()._url
    schemas = None if force else _load_cache(url)
    if schemas is None:
        try:
            schemas = await fetch_redmine_tool_schemas()
            _save_cache(url, schemas)
        except Exception as e:
            logger.warning("[redmine-mcp] tool discovery failed (skipping): %s", e)
            return []
    return register_redmine_tools(schemas)
