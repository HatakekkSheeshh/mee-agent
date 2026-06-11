"""One-way sync: Postgres project state → AgentBase Memory (project_facts).

For each non-soft-deleted Meeting (project): gather project_summary_json + each
recording's mom_json, content-hash the inputs, compare to the hash embedded in the
project's latest AgentBase record, and — only if changed — distill a condensed
current-state text via the LLM and insert it as a new record.

v1 is INSERT-ONLY, newest-wins: AgentBase record DELETE is denied for our service
account, so we never overwrite; change detection keeps churn to ~1 record per real
change. AgentBase is a rebuildable cache; Postgres stays the system of record.

Usage:
    venv/bin/python scripts/sync_memory.py            # live: distill + insert changed projects
    venv/bin/python scripts/sync_memory.py --dry-run  # print distilled text + hash, write nothing

Spec: docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md
Mirrors scripts/backfill_embeddings.py (async engine, per-project logging).
"""
import argparse
import asyncio
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True, interpolate=False)

from sqlalchemy import select

from meeting.db.base import AsyncSessionLocal, async_engine
from meeting.db.models import Meeting
from meeting.services.memory_sync_runner import sync_project

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_memory")


async def main(dry_run: bool) -> int:
    counts: dict[str, int] = {}

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Meeting.id, Meeting.title).where(Meeting.deleted_at.is_(None))
        )).all()
        logger.info(f"{len(rows)} non-deleted project(s) to consider"
                    f"{' (DRY-RUN)' if dry_run else ''}")

        for mid, title in rows:
            label = f"[{mid}] {title!r}"
            try:
                result = await sync_project(session, mid, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001 — one project must not abort the run
                counts["error"] = counts.get("error", 0) + 1
                logger.error(f"{label}: FAILED — {e}")
                continue

            action = result["action"]
            counts[action] = counts.get(action, 0) + 1
            if action == "sync" and dry_run:
                logger.info(f"{label}: WOULD SYNC (hash={result['hash'][:12]}…)\n"
                            f"----- distilled -----\n{result['text']}\n---------------------")
            elif action == "sync":
                logger.info(f"{label}: SYNCED (hash={result['hash'][:12]}…)")
            elif action == "skip":
                logger.info(f"{label}: unchanged — skip")
            elif action == "empty":
                logger.info(f"{label}: no summary/MoM content — skip")
            elif action == "disabled":
                logger.info(f"{label}: MEMORY_ID not set — skip")
            # "missing" = deleted between listing and fetch; ignore silently

    await async_engine.dispose()
    logger.info(
        "Done: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        + (" (DRY-RUN, nothing written)" if dry_run else "")
    )
    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sync Postgres project state → AgentBase Memory")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print distilled text + hash for changed projects; write nothing.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)) or 0)
