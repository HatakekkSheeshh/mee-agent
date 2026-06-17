"""Dump the 3 kinds of AgentBase memory this app uses, for one meeting + user.

  1. project-state distillation   → project_facts/mee-user      ([mee-sync …] blob)
  2. project-scoped remembered    → project_facts/<meeting_id>  ([mee-fact scope=project …])
  3. user-scoped remembered       → user_prefs/<ms_oid>         ([mee-fact scope=user …])

Usage:
    venv/bin/python scripts/dump_agent_memory.py <meeting_id> [ms_oid]

Reads MEMORY_ID + GreenNode creds from .env (same loader as the app). Best-effort:
prints "(none)"/empty rather than raising when memory is unconfigured or empty.
"""
from __future__ import annotations

import pathlib
import sys

import dotenv

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
dotenv.load_dotenv(PROJECT_ROOT / ".env", override=True, interpolate=False)
sys.path.append(str(PROJECT_ROOT))

from src import memory_client as mc  # noqa: E402


def main() -> int:
    meeting_id = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    ms_oid = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    print(f"\n=== 1. distillation (project_facts/mee-user, project={meeting_id or '-'}) ===")
    rec = mc.search_project_record(meeting_id) if meeting_id else None
    print(mc.strip_project_marker(rec.get("memory")) if rec else "(none)")

    print(f"\n=== 2. project facts (project_facts/{meeting_id or '-'}) ===")
    pfacts = mc.list_fact_records(mc.fact_namespace("project", meeting_id)) if meeting_id else []
    for f in pfacts:
        print(" -", f)
    if not pfacts:
        print("(none)")

    print(f"\n=== 3. user facts (user_prefs/{ms_oid or '-'}) ===")
    ufacts = mc.list_fact_records(mc.fact_namespace("user", ms_oid)) if ms_oid else []
    for f in ufacts:
        print(" -", f)
    if not ufacts:
        print("(none)")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)
