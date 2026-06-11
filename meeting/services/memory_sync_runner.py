"""DB+network wiring for the Postgres→AgentBase project-state sync.

`memory_sync.py` stays pure (hash + distill + orchestration). This module is the
impure shell: it pulls a project's data from Postgres, builds the LLM + AgentBase
seams, and runs `sync_one_project`. Used by BOTH:

  - scripts/sync_memory.py  — batch sweep over all projects (cron/manual);
  - the write-path hooks      — `schedule_project_sync` fires a best-effort,
    non-blocking re-sync after a recording's MoM or a project summary is saved
    (event-driven, option (b) in the design — now that v1 batch is proven).

AgentBase is a rebuildable cache: every path is best-effort and must never block
or break MoM/summary generation. With MEMORY_ID unset the sync is a no-op.

Spec: docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db import repositories as repo
from meeting.db.base import AsyncSessionLocal
from meeting.memory_client import (
    parse_project_marker,
    search_project_record,
    upsert_project_record,
)
from meeting.services.memory_sync import distill_project_state, sync_one_project

logger = logging.getLogger(__name__)


def _llm_client():
    """Per-service OpenAI client (CLAUDE.md: no shared singleton).

    Uses the general LLM_* config — the dedicated gemma-4-31b-it MaaS deployment
    403s for our key, so distillation runs on LLM_BASE_URL/LLM_MODEL.
    """
    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )
    model = os.getenv("LLM_MODEL", "google/gemma-4-31b-it")
    return client, model


def _existing_hash(project_id: str) -> str | None:
    """source_hash embedded in the project's latest AgentBase record, or None."""
    rec = search_project_record(project_id)
    if not rec:
        return None
    marker = parse_project_marker(rec.get("memory"))
    return marker["hash"] if marker else None


async def sync_project(
    session: AsyncSession, meeting_id, *, dry_run: bool = False
) -> dict:
    """Sync ONE project. Returns {"action", "hash"?, "text"?}.

    action ∈ {"disabled","missing","empty","skip","sync"}. The blocking work
    (AgentBase browse + LLM distill + insert) runs in a worker thread so this
    never stalls the event loop when called from a request/graph node.
    """
    if not os.getenv("MEMORY_ID"):
        return {"action": "disabled"}

    mid = meeting_id if isinstance(meeting_id, uuid.UUID) else uuid.UUID(str(meeting_id))
    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        return {"action": "missing"}

    recordings = sorted((meeting.recordings or []), key=lambda r: str(r.started_at or ""))
    moms = [r.mom_json for r in recordings]
    client, model = _llm_client()
    title = meeting.title
    project_summary = meeting.project_summary_json
    pid = str(meeting.id)

    def distill(summary, ms):
        return distill_project_state(summary, ms, client=client, model=model)

    def upsert(project_id, text, source_hash):
        return upsert_project_record(project_id, text, source_hash, title=title)

    # sync_one_project is pure-sync but its injected seams do network + LLM I/O —
    # offload the whole thing to a thread to keep the caller's loop responsive.
    return await asyncio.to_thread(
        sync_one_project,
        project_id=pid,
        project_summary=project_summary,
        moms=moms,
        get_existing_hash=_existing_hash,
        distill=distill,
        upsert_record=upsert,
        dry_run=dry_run,
    )


async def _run_project_sync_bg(meeting_id: str) -> None:
    """Background re-sync with its OWN session (the caller's may be closed/committed)."""
    try:
        async with AsyncSessionLocal() as session:
            result = await sync_project(session, meeting_id)
        logger.info(
            f"[memory-sync] event-driven sync for {meeting_id}: {result.get('action')}"
        )
    except Exception as e:  # noqa: BLE001 — sync must never break the write path
        logger.warning(f"[memory-sync] event-driven sync for {meeting_id} failed: {e}")


def schedule_project_sync(meeting_id) -> bool:
    """Fire-and-forget a re-sync after a project's data changed. Best-effort.

    Schedules a background task on the running loop (its own DB session). Returns
    True if scheduled, False if no loop or AgentBase disabled — callers ignore it.
    Never raises.
    """
    if not meeting_id or not os.getenv("MEMORY_ID"):
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    loop.create_task(_run_project_sync_bg(str(meeting_id)))
    return True
