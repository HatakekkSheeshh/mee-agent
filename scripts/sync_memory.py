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

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from meeting.db.base import AsyncSessionLocal, async_engine
from meeting.db.models import Meeting
from meeting.memory_client import (
    parse_project_marker,
    search_project_record,
    upsert_project_record,
)
from meeting.services.memory_sync import distill_project_state, sync_one_project

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_memory")


def _llm_client():
    """Per-service OpenAI client (CLAUDE.md: no shared singleton).

    Uses the general LLM_* config (same vars as note_generator / the chat agent).
    The dedicated gemma-4-31b-it MaaS deployment 403s for our key, so distillation
    runs on whatever the general LLM_BASE_URL/LLM_MODEL point at.
    """
    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )
    model = os.getenv("LLM_MODEL", "google/gemma-4-31b-it")
    return client, model


def _existing_hash(project_id: str) -> str | None:
    """Read the source_hash embedded in the project's latest AgentBase record."""
    rec = search_project_record(project_id)
    if not rec:
        return None
    marker = parse_project_marker(rec.get("memory"))
    return marker["hash"] if marker else None


async def main(dry_run: bool) -> int:
    client, model = _llm_client()

    def distill(summary, moms):
        return distill_project_state(summary, moms, client=client, model=model)

    # Per-project title is added deterministically to the record body (the LLM
    # echoes the project name inconsistently). Map id→title for the upsert wrapper.
    title_by_id: dict[str, str] = {}

    def upsert(project_id, text, source_hash):
        return upsert_project_record(
            project_id, text, source_hash, title=title_by_id.get(project_id)
        )

    counts = {"sync": 0, "skip": 0, "empty": 0, "error": 0}

    async with AsyncSessionLocal() as session:
        meetings = (await session.execute(
            select(Meeting)
            .where(Meeting.deleted_at.is_(None))
            .options(selectinload(Meeting.recordings))
        )).scalars().all()
        logger.info(f"{len(meetings)} non-deleted project(s) to consider"
                    f"{' (DRY-RUN)' if dry_run else ''}")

        for m in meetings:
            pid = str(m.id)
            title_by_id[pid] = m.title
            recordings = sorted(
                (m.recordings or []),
                key=lambda r: str(r.started_at or ""),
            )
            moms = [r.mom_json for r in recordings]
            try:
                result = sync_one_project(
                    project_id=pid,
                    project_summary=m.project_summary_json,
                    moms=moms,
                    get_existing_hash=_existing_hash,
                    distill=distill,
                    upsert_record=upsert,
                    dry_run=dry_run,
                )
            except Exception as e:  # noqa: BLE001 — one project must not abort the run
                counts["error"] += 1
                logger.error(f"[{pid}] {m.title!r}: FAILED — {e}")
                continue

            action = result["action"]
            counts[action] = counts.get(action, 0) + 1
            label = f"[{pid}] {m.title!r}"
            if action == "skip":
                logger.info(f"{label}: unchanged — skip")
            elif action == "empty":
                logger.info(f"{label}: no summary/MoM content — skip")
            elif action == "sync":
                if dry_run:
                    logger.info(f"{label}: WOULD SYNC (hash={result['hash'][:12]}…)\n"
                                f"----- distilled -----\n{result['text']}\n---------------------")
                else:
                    logger.info(f"{label}: SYNCED (hash={result['hash'][:12]}…)")

    await async_engine.dispose()
    logger.info(f"Done: {counts['sync']} synced, {counts['skip']} unchanged, "
                f"{counts['empty']} empty, {counts['error']} errored"
                f"{' (DRY-RUN, nothing written)' if dry_run else ''}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sync Postgres project state → AgentBase Memory")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print distilled text + hash for changed projects; write nothing.")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)) or 0)
