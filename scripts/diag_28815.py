"""Diagnostic: is stale-table issue #28815 null data, or LLM output truncation?

Fetches #28815 two ways and prints ONLY issue fields (never the API key):
  1. get_redmine_issue_by_id(28815)        — ground truth for one issue
  2. get_stale_issues(...) then locate 28815 — how the read tool feeds the LLM

Run: ECC_GATEGUARD=off venv/bin/python scripts/diag_28815.py "AI Innovation Projects" [days]
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

from dotenv import load_dotenv

load_dotenv(override=True, interpolate=False)
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.services.redmine_mcp_client import get_redmine_mcp_client  # noqa: E402

FIELDS = ("id", "subject", "assigned_to", "updated_on", "status", "tracker", "author")


def _row_summary(row: dict) -> dict:
    return {k: row.get(k) for k in FIELDS if k in row} or {"<keys>": list(row.keys())}


async def main() -> None:
    project = sys.argv[1] if len(sys.argv) > 1 else "AI Innovation Projects"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 14
    client = get_redmine_mcp_client()

    print("=== 1) get_redmine_issue_by_id(28815) — ground truth ===")
    by_id = await client.call_tool("get_redmine_issue_by_id", {"issue_id": 28815})
    print(json.dumps(by_id, ensure_ascii=False, indent=2)[:3000])

    print(f"\n=== 2) get_stale_issues(project={project!r}, days={days}) ===")
    stale = await client.call_tool(
        "get_stale_issues", {"project_name": project, "days": days}
    )
    # Result shape unknown — could be {"issues": [...]}, {"result": [...]}, or a bare list.
    print(f"  top-level type: {type(stale).__name__}; keys: "
          f"{list(stale.keys()) if isinstance(stale, dict) else 'N/A (list)'}")
    rows = None
    if isinstance(stale, list):
        rows = stale
    elif isinstance(stale, dict):
        for k in ("issues", "result", "data", "items", "stale_issues"):
            v = stale.get(k)
            if isinstance(v, list):
                rows = v
                print(f"  rows under key: {k!r}")
                break
    if rows is None:
        print("  !! could not find a list of rows — full dump:")
        print(json.dumps(stale, ensure_ascii=False, indent=2)[:4000])
        return

    print(f"  total rows: {len(rows)}")
    if rows:
        print(f"  row[0] keys: {list(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0])}")

    def _id(r):
        return r.get("issue_id") if isinstance(r, dict) else None

    # Scan EVERY row for null/blank in the three reported-blank fields.
    blank_fields = ("subject", "assigned_to", "last_updated")
    print("\n  --- per-row null/blank scan (all rows) ---")
    any_blank = False
    for r in rows:
        if not isinstance(r, dict):
            continue
        blanks = [k for k in blank_fields if r.get(k) in (None, "", [])]
        flag = f"  ⚠ BLANK: {blanks}" if blanks else ""
        if blanks:
            any_blank = True
        print(f"   #{_id(r)} subj={r.get('subject')!r} assignee={r.get('assigned_to')!r} "
              f"upd={r.get('last_updated')!r}{flag}")
    if not any_blank:
        print("  ✓ NO row has a blank subject/assignee/last_updated — data is complete.")

    match = next((r for r in rows if _id(r) == 28815), None)
    print("\n  --- #28815 inside get_stale_issues ---")
    if match is None:
        print(f"  NOT in this stale result. issue_ids: {[_id(r) for r in rows if isinstance(r, dict)]}")
    else:
        print(f"  full row: {json.dumps(match, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
