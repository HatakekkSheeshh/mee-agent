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


from meeting.services.redmine_mcp_client import get_redmine_mcp_client  # noqa: E402


async def main() -> None:
    url = os.getenv("MCP_REDMINE_URL", "")
    key = os.getenv("REDMINE_API_KEY", "")
    print(f">>> MCP_REDMINE_URL={url!r}  REDMINE_API_KEY={'set' if key else 'MISSING'}")
    client = get_redmine_mcp_client()

    # 1) list_tools — proves auth + transport + the tool surface.
    print("\n=== list_tools ===")
    try:
        async with client._session() as session:
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  - {t.name}: {(t.description or '')[:60]}")
    except Exception as e:
        print(f"  ❌ list_tools failed: {e}")
        print("  (check REDMINE_API_KEY auth, MCP_REDMINE_URL, and that `mcp` is installed)")
        return

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
