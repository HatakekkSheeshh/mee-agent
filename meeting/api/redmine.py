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

# Minimum Redmine MCP tools we expect registered (live surface ~14; README
# documented 5). Treated as a FLOOR (>=) — fewer means discovery failed/drifted;
# more is fine (the surface can grow).
EXPECTED_REDMINE_TOOL_COUNT = 14

# Hints that mark a registered tool as part of the Redmine MCP surface (tools are
# dynamically registered, so we count by name rather than a static list).
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
    tools_ok = key_present and registered_tool_count >= EXPECTED_REDMINE_TOOL_COUNT
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


def _pm_agent_configured(key_present: bool) -> bool:
    # pm-agent now authenticates via the SAME agent-identity outbound key
    # (provider "redmine") — no separate TOKEN_AUTHEN_PM_AGENT. Configured =
    # its URL is set AND the per-user identity key resolved. Presence check, not
    # a live A2A handshake (see plan scope note).
    return bool(os.getenv("PM_AGENT_URL", "")) and key_present


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
            if result.apikey:
                # Register Redmine tools now (using the key we just fetched) so
                # the count below reflects reality even before the first chat turn.
                await ts.ensure_redmine_tools_with_key(result.apikey)
        except Exception as e:  # AgentBase unreachable → unknown, fail soft
            logger.warning("redmine_status: identity probe failed: %s", e)
    return build_redmine_status(
        key_present=key_present,
        registered_tool_count=count_registered_redmine_tools(),
        pm_agent_ok=_pm_agent_configured(key_present),
        gate_url=gate_url,
    )
