"""Probe: what does the live pm-agent return for a project-scoped list-issues ask?

Isolates the layer for the bug "liệt kê issue trong project AI Innovation Project
doesn't scope to that project": our pm_call sends the user text VERBATIM as the
A2A text part (verified in code), so if pm-agent still ignores / asks for the
project, the loss is on the pm-agent side, not in this repo.

Run: ECC_GATEGUARD=off venv/bin/python scripts/probe_pm_list_issues.py
"""
from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv(override=True, interpolate=False)

from src.services.pm_agent_client import get_pm_agent_client  # noqa: E402

PROMPT = "liệt kê issue trong project AI Innovation Project"


async def main() -> None:
    client = get_pm_agent_client()
    print(f">>> sending verbatim text part: {PROMPT!r}")
    result = await client.send_message(PROMPT)
    print(f"state         = {result.state}")
    print(f"task_id       = {result.task_id}")
    print(f"need_approval = {result.need_approval}")
    print(f"text (first 1200 chars):\n{result.text[:1200]}")
    # Don't leave a dangling input_required task on the shared pm-agent.
    if result.state == "input_required" and result.task_id:
        await client.cancel(result.task_id)
        print("(cancelled the probe task)")


if __name__ == "__main__":
    asyncio.run(main())
