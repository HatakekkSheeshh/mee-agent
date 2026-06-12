"""Redmine MCP client — streamable-http transport to the deployed MCP server.

Simplified port of pm-agent's src/mcp_server/mcp_http_client.py. Mee uses a
single env REDMINE_API_KEY as the Bearer token (the token IS the Redmine API
key; the server validates it against /users/current.json). No per-user auth.

A fresh streamable-http session is opened per tool call (sessions are cheap and
the key is fixed). Result parsing prefers FastMCP's structuredContent, unwraps
its {"result": ...} wrapper, surfaces isError as {"error": ...}, and falls back
to text->JSON.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _extract_text(content_blocks: list) -> str:
    """Concatenate text from a CallToolResult.content list."""
    if not content_blocks:
        return ""
    parts: list[str] = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _parse_call_result(result: Any) -> dict:
    """Normalize an mcp CallToolResult into a plain dict (pure; unit-tested)."""
    if getattr(result, "isError", False):
        return {"error": _extract_text(getattr(result, "content", None) or []) or "Unknown MCP tool error"}

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP wraps non-dict returns as {"result": <value>}; unwrap it. Keep
        # the dict return type by re-wrapping a scalar as {"result_value": ...}.
        if set(structured.keys()) == {"result"}:
            inner = structured["result"]
            return inner if isinstance(inner, (dict, list)) else {"result_value": inner}
        return structured

    text = _extract_text(getattr(result, "content", None) or [])
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}


class RedmineMcpClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        if not base_url:
            raise ValueError("MCP_REDMINE_URL is not configured")
        url = base_url.rstrip("/")
        if not url.endswith("/mcp"):
            url = f"{url}/mcp"
        self._url = url
        self._api_key = api_key
        self._timeout = timeout

    @asynccontextmanager
    async def _session(self):
        # Imported lazily so importing this module (and the whole services
        # package, which conftest does) never requires `mcp` to be installed
        # unless a Redmine tool is actually invoked.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with streamablehttp_client(self._url, headers=headers, timeout=self._timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def call_tool(self, name: str, arguments: dict) -> dict:
        logger.info("[redmine-mcp] call_tool %s args=%s", name, arguments)
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments)
        except Exception as e:  # transport / auth / server error
            logger.exception("[redmine-mcp] call_tool %s failed", name)
            return {"error": f"redmine mcp error: {e}"}
        return _parse_call_result(result)


_singleton: Optional[RedmineMcpClient] = None


def get_redmine_mcp_client() -> RedmineMcpClient:
    """Lazy env singleton (mirrors get_pm_agent_client)."""
    global _singleton
    if _singleton is None:
        _singleton = RedmineMcpClient(
            base_url=os.getenv("MCP_REDMINE_URL", ""),
            api_key=os.getenv("REDMINE_API_KEY", ""),
        )
    return _singleton
