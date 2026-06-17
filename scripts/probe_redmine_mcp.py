"""Probe: does the deployed Redmine MCP server answer over streamable-http?

Verifies the LAYER (auth + transport + tool surface) independently of the chat
graph: connects to MCP_REDMINE_URL with Bearer REDMINE_API_KEY, lists tools, and
calls the two READ tools (no writes). If this works, the chat agent's Redmine
tools will too; if it 401s/empties, the loss is config (key/url), not our code.

Needs: `mcp` installed (pip install mcp>=1.25.0) + real .env + network.
Run: ECC_GATEGUARD=off venv/bin/python scripts/probe_redmine_mcp.py [project_name]
"""
from __future__ import annotations

import asyncio
import os
import sys
import pathlib
from dotenv import load_dotenv

load_dotenv(override=True, interpolate=False)
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))  # for imports``


import json  # noqa: E402

from src.services.redmine_mcp_client import get_redmine_mcp_client  # noqa: E402
from src.services.tools import get_tool  # noqa: E402
from src.services.tools.redmine import (  # noqa: E402
    fetch_redmine_tool_schemas,
    is_write_tool,
    register_redmine_tools,
)


async def main() -> None:
    url = os.getenv("MCP_REDMINE_URL", "")
    key = os.getenv("REDMINE_API_KEY", "")
    print(f">>> MCP_REDMINE_URL={url!r}  REDMINE_API_KEY={'set' if key else 'MISSING'}")
    client = get_redmine_mcp_client()

    # 1) Fetch the FULL tool surface + every inputSchema (no inference).
    print("\n=== tool surface (name | read/write | required | full inputSchema) ===")
    try:
        schemas = await fetch_redmine_tool_schemas()
    except Exception as e:
        print(f"  ❌ fetch_redmine_tool_schemas failed: {e}")
        print("  (check REDMINE_API_KEY auth, MCP_REDMINE_URL, and that `mcp` is installed)")
        return

    for s in schemas:
        name = s["name"]
        sch = s.get("inputSchema") or {}
        required = sch.get("required") or []
        kind = "WRITE" if is_write_tool(name) else "read"
        print(f"\n  ## {name}  [{kind}]  required={required}")
        print("  " + json.dumps(sch, ensure_ascii=False, indent=2).replace("\n", "\n  "))

    # 2) Registration dry-run — exactly what the app registers at startup.
    print("\n=== register_redmine_tools dry-run (side_effect per tool) ===")
    names = register_redmine_tools(schemas)
    for n in names:
        spec = get_tool(n)
        print(f"  - {n}: side_effect={spec['side_effect']} props={list((spec['schema'].get('properties') or {}).keys())}")

    # 3) Explicit subtask check: is create_redmine_issue parent-aware?
    print("\n=== subtask capability (explicit) ===")
    create = next((s for s in schemas if s["name"] == "create_redmine_issue"), None)
    has_subtask_tool = any("subtask" in s["name"].lower() for s in schemas)
    parent_field = bool(create and "parent_issue_id" in ((create.get("inputSchema") or {}).get("properties") or {}))
    print(f"  separate *subtask* tool present: {has_subtask_tool}")
    print(f"  create_redmine_issue has parent_issue_id field: {parent_field}")

    # 2) get_redmine_projects — a real READ round-trip through our call_tool path.
    print("\n=== get_redmine_projects ===")
    projects = await client.call_tool("get_redmine_projects", {})
    print(f"  {projects}")

    # 3) Optional: list issues for a project passed on the CLI.
    if len(sys.argv) > 1:
        project = sys.argv[1]
        print(f"\n=== list_redmine_issue project_name={project!r} ===")
        issues = await client.call_tool("list_redmine_issue", {"project_name": project})
        print(f"  {issues}")


if __name__ == "__main__":
    asyncio.run(main())
