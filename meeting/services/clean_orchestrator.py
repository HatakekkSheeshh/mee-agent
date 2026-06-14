"""Background cleaner orchestrator (in-process asyncio).

After import_transcript or diarize-result completes, we spawn a background
task that runs the cleaner LLM and persists `recording.clean_segments`.
By the time the user clicks Clean, the result is usually already in DB →
instant load, no waiting.

Design:
  - `_active_tasks: dict[recording_id_str, asyncio.Task]` — registry of
    in-flight tasks. Multiple triggers for the same recording share the
    existing task instead of starting a duplicate.
  - `trigger_background(recording_id)` — fire-and-forget. Idempotent.
  - Each background task uses a FRESH DB session (don't borrow the
    caller's request session — the request finishes long before the
    background task does).
  - Skip if `recording.clean_segments` already exists (cache hit).

When to migrate to RabbitMQ / Celery: see PROGRESS_SUMMARY 2.2.C/D.
For hackathon scope, asyncio in-process is enough.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.orm.attributes import flag_modified

from meeting.db.base import AsyncSessionLocal
from meeting.db import repositories as repo
from meeting.services.transcript_cleaner import clean_transcript

logger = logging.getLogger(__name__)

# Holds EITHER an asyncio.Task (in-process fallback) or a Celery AsyncResult
# (preferred path when RabbitMQ is up). Readers go through is_inflight() /
# wait_for_inflight() so they don't need to know which type is in there.
_active_tasks: dict[str, object] = {}


# Registry of in-process "local tasks" so /api/tasks/{id} can report
# state when Celery is unreachable. Keyed by a uuid we hand back to FE
# from the dispatching endpoint — FE polls the same way it does for
# Celery task_ids. Entry value: {"state", "error", "result"}.
_local_task_state: dict[str, dict] = {}


def dispatch_local_task(coro_factory, prefix: str = "local") -> str:
    """Spawn a background asyncio task + register it so FE can poll status.

    `coro_factory` is a zero-arg async callable so we can defer coroutine
    creation until inside the running loop (matters when callers build
    closures around request-scoped state).

    Returns a task_id string the endpoint hands back to FE. FE polls
    /api/tasks/{id} which dispatches to `get_local_task_state` below.
    """
    import asyncio
    task_id = f"{prefix}-{uuid.uuid4().hex}"
    _local_task_state[task_id] = {"state": "PENDING"}

    async def _wrapped():
        _local_task_state[task_id] = {"state": "STARTED"}
        try:
            result = await coro_factory()
            _local_task_state[task_id] = {"state": "SUCCESS", "result": result}
        except Exception as e:
            logger.exception(f"[local_task {task_id}] failed")
            _local_task_state[task_id] = {"state": "FAILURE", "error": str(e)}

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (called from non-async path). Bail — caller
        # should be inside a FastAPI request which always has one.
        _local_task_state[task_id] = {
            "state": "FAILURE",
            "error": "no event loop available",
        }
        return task_id
    loop.create_task(_wrapped())
    return task_id


def get_local_task_state(task_id: str) -> dict | None:
    """Return Celery-compatible {state, result?, error?} for a local task.

    Returns None if task_id isn't in the local registry (caller should
    then try the Celery backend).
    """
    entry = _local_task_state.get(task_id)
    if entry is None:
        return None
    return {"task_id": task_id, **entry}

# Per-recording progress info for the FE progress bar.
# {recording_id: {phase, current_chunk, total_chunks, started_at_ms}}
# Updated by _run_clean as it goes through chunks.
_progress: dict[str, dict] = {}


def get_progress(recording_id: str) -> dict | None:
    """Read current progress for FE polling. None if no active task."""
    return _progress.get(recording_id)


def is_inflight(recording_id: str) -> bool:
    """True when a background clean is still running for this recording.

    Handles both task types in `_active_tasks`. Also prunes finished
    Celery handles — they have no done-callback to clean up themselves
    the way asyncio.Task does.
    """
    t = _active_tasks.get(recording_id)
    if t is None:
        return False
    # asyncio.Task — has .done()
    if hasattr(t, "done"):
        if t.done():  # type: ignore[attr-defined]
            _active_tasks.pop(recording_id, None)
            return False
        return True
    # Celery AsyncResult — has .ready()
    if hasattr(t, "ready"):
        if t.ready():  # type: ignore[attr-defined]
            _active_tasks.pop(recording_id, None)
            return False
        return True
    # Unknown — pretend not running so caller doesn't deadlock.
    return False


async def wait_for_inflight(recording_id: str, timeout_s: float = 600.0) -> None:
    """Block until the active clean for this recording finishes (or timeout).

    Used by /clean endpoint to share a single in-flight clean across
    multiple user clicks. Handles both asyncio.Task and Celery AsyncResult.
    Swallows exceptions — the background task logs them itself, and the
    caller will fall through to a fresh /clean attempt after this returns.
    """
    t = _active_tasks.get(recording_id)
    if t is None:
        return
    if hasattr(t, "done"):  # asyncio.Task
        try:
            await asyncio.wait_for(t, timeout=timeout_s)  # type: ignore[arg-type]
        except Exception:
            pass
        return
    if hasattr(t, "ready"):  # Celery AsyncResult — poll
        import time as _time
        deadline = _time.monotonic() + timeout_s
        while _time.monotonic() < deadline:
            if t.ready():  # type: ignore[attr-defined]
                _active_tasks.pop(recording_id, None)
                return
            await asyncio.sleep(2.0)


def trigger_background(recording_id: str) -> None:
    """Fire-and-forget. Hand the cleaner off to the Celery worker so the
    LLM call (slow + flaky 504s from MaaS) doesn't block the FastAPI event
    loop or this request. Returns immediately. Safe to call multiple times
    for the same recording — Celery doesn't dedupe so we still check the
    in-process registry, but the registry now just tracks "dispatched"
    rather than "running here".

    Falls back to the old in-process asyncio path when RabbitMQ is down,
    so dev setups without Docker still work.
    """
    if recording_id in _active_tasks:
        logger.info(
            f"[clean_orchestrator] {recording_id} already dispatched, skip trigger"
        )
        return

    # Preferred path: dispatch to Celery worker. Same task as /clean uses
    # → identical retry + timeout behaviour.
    try:
        from meeting.celery_app import is_broker_reachable
        if is_broker_reachable():
            from meeting.tasks import clean_recording_task
            ar = clean_recording_task.delay(recording_id, False)
            logger.info(
                f"[clean_orchestrator] dispatched clean_recording_task "
                f"id={ar.id} for {recording_id} (background pre-clean)"
            )
            # Mark as dispatched — get_progress / clean-status still need
            # to report "running" until the worker finishes. Store an
            # async-result handle so cleanup can drop the entry.
            _active_tasks[recording_id] = ar  # type: ignore[assignment]
            return
    except Exception as e:
        logger.warning(
            f"[clean_orchestrator] Celery dispatch failed ({e}), "
            f"falling back to in-process asyncio"
        )

    # Fallback: original in-process asyncio path. Used when broker is
    # unreachable (eg. dev setup without RabbitMQ).
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            f"[clean_orchestrator] no running loop, skip background trigger "
            f"for {recording_id}"
        )
        return

    task = loop.create_task(_run_clean(recording_id))
    _active_tasks[recording_id] = task

    def _cleanup(_t: asyncio.Task) -> None:
        _active_tasks.pop(recording_id, None)
        _progress.pop(recording_id, None)

    task.add_done_callback(_cleanup)
    logger.info(
        f"[clean_orchestrator] triggered IN-PROCESS background clean "
        f"for {recording_id} (broker unreachable)"
    )


async def _run_clean(recording_id: str) -> None:
    """Actual cleaner run with fresh DB session. Logs and swallows errors."""
    try:
        rid = uuid.UUID(recording_id)
        async with AsyncSessionLocal() as session:
            try:
                recording = await repo.get_recording(session, rid)
                if not recording:
                    logger.warning(
                        f"[clean_orchestrator] recording {recording_id} not found"
                    )
                    return
                # Skip if already cleaned (avoid duplicate work on race)
                if recording.clean_segments:
                    logger.info(
                        f"[clean_orchestrator] {recording_id} already has clean — skip"
                    )
                    return

                # Source priority — same as /clean endpoint
                if recording.diarized_text and recording.diarized_text.strip():
                    raw_text = recording.diarized_text
                else:
                    raw_text = await repo.join_recording_transcript(session, rid)
                if not raw_text.strip():
                    logger.info(
                        f"[clean_orchestrator] {recording_id} has no transcript — skip"
                    )
                    return

                meeting = await repo.get_meeting(session, recording.meeting_id)

                # Resolve effective LLM profile (recording → meeting → default).
                from meeting.services.model_registry import resolve_llm
                llm_profile = resolve_llm(
                    recording_choice=recording.llm_model,
                    meeting_choice=getattr(meeting, "llm_model", None) if meeting else None,
                )
                logger.info(
                    f"[clean_orchestrator] {recording_id} using LLM={llm_profile.get('id')} "
                    f"({llm_profile.get('model')})"
                )

                # Attendees from recording (project-level removed in migration 0012)
                attendees_str = ""
                if recording.attendees:
                    attendees_str = ", ".join(
                        a.get("name", "")
                        for a in recording.attendees
                        if isinstance(a, dict)
                    )

                # Vocab: meeting (project default) + recording (session-specific)
                vocab_parts = [
                    (meeting.vocab_hints if meeting else None) or "",
                    recording.vocab_hints or "",
                ]
                merged_vocab = (
                    ", ".join(p.strip() for p in vocab_parts if p.strip()) or None
                )

                # Ensure phonetic_examples_json is fresh w.r.t. current vocab —
                # regenerate (1 LLM call) only if vocab hash mismatched / missing.
                # Cached value reused across all chunks of THIS recording.
                phonetic_mappings: list[dict] = []
                if merged_vocab:
                    from meeting.services.phonetic_generator import (
                        generate_phonetic_mappings, needs_regeneration,
                    )
                    cached = recording.phonetic_examples_json or {}
                    if needs_regeneration(merged_vocab, cached):
                        logger.info(
                            f"[clean_orchestrator] regenerating phonetic mappings "
                            f"for {recording_id} (vocab changed)"
                        )
                        new_phon = await asyncio.to_thread(
                            generate_phonetic_mappings, merged_vocab,
                            llm_profile=llm_profile,
                        )
                        await repo.save_recording_phonetic(session, rid, new_phon)
                        await session.commit()  # commit before long cleaner call
                        phonetic_mappings = new_phon.get("mappings", [])
                    else:
                        phonetic_mappings = cached.get("mappings", [])

                # Pre-mapped speakers via voice match
                pre_mapped: dict[str, str] = {}
                if recording.speaker_embeddings:
                    from meeting.services.speaker_matcher import (
                        match_clusters_to_names,
                    )
                    user = await repo.get_or_create_dev_user(session)
                    pre_mapped = await match_clusters_to_names(
                        session,
                        user_id=user.id,
                        speaker_embeddings=recording.speaker_embeddings,
                    )
                # Merge in any user-applied renames from a prior clean_segments
                # run — without this, regenerate clean clobbers manual renames
                # because the LLM only sees voiceprint-derived pre_mapped and
                # outputs cluster ids for any cluster the voiceprint match
                # didn't cover.
                prev_clean = recording.clean_segments or {}
                prev_cluster_map = (
                    prev_clean.get("cluster_mapping")
                    if isinstance(prev_clean, dict) else None
                )
                if isinstance(prev_cluster_map, dict):
                    for cid, name in prev_cluster_map.items():
                        nm = (name or "").strip()
                        # Skip placeholders so they don't override real voice
                        # matches; only carry forward explicit user renames.
                        if nm and nm.lower() not in ("unknown", ""):
                            pre_mapped.setdefault(cid, nm)

                # Estimate chunk count for progress reporting (cleaner internally
                # chunks at MAX_TRANSCRIPT_CHARS = 14_000). Approximate so FE
                # can show "1/N" progress.
                from meeting.services.transcript_cleaner import MAX_TRANSCRIPT_CHARS
                est_total = max(
                    1, (len(raw_text) + MAX_TRANSCRIPT_CHARS - 1) // MAX_TRANSCRIPT_CHARS
                )
                import time
                _progress[recording_id] = {
                    "phase": "cleaning",
                    "current_chunk": 0,
                    "total_chunks": est_total,
                    "started_at_ms": int(time.time() * 1000),
                    "raw_chars": len(raw_text),
                }

                # Cleaner LLM is sync (uses requests) — run in threadpool
                # so we don't block the event loop for 30s-2min.
                logger.info(
                    f"[clean_orchestrator] running cleaner for {recording_id} "
                    f"({len(raw_text)} chars, ~{est_total} chunks)"
                )
                result = await asyncio.to_thread(
                    clean_transcript,
                    raw_text=raw_text,
                    attendees=attendees_str,
                    pre_mapped=pre_mapped or None,
                    vocab_hints=merged_vocab,
                    phonetic_examples=phonetic_mappings or None,
                    llm_profile=llm_profile,
                )
                _progress[recording_id] = {
                    "phase": "saving",
                    "current_chunk": est_total,
                    "total_chunks": est_total,
                    "started_at_ms": _progress[recording_id]["started_at_ms"],
                    "raw_chars": len(raw_text),
                }
                if "error" in result:
                    logger.warning(
                        f"[clean_orchestrator] cleaner failed for "
                        f"{recording_id}: {result['error']}"
                    )
                    return

                # Re-fetch recording in case it changed during the LLM call
                recording = await repo.get_recording(session, rid)
                if not recording:
                    return
                # Don't clobber if user manually cleaned during our LLM run
                if recording.clean_segments:
                    logger.info(
                        f"[clean_orchestrator] {recording_id} cleaned by user during "
                        f"our LLM run — skip save"
                    )
                    return
                # Don't save empty results — likely all chunks failed (rate
                # limit, network). User clicking Clean tab will retry.
                segs = result.get("segments", [])
                if not segs:
                    logger.warning(
                        f"[clean_orchestrator] {recording_id} produced 0 segments "
                        f"(all chunks failed — likely rate limit). Not saving."
                    )
                    return

                existing: dict = recording.clean_segments or {}
                existing["segments"] = segs
                existing["cluster_mapping"] = result.get("cluster_mapping", {})
                for cid, name in pre_mapped.items():
                    existing["cluster_mapping"][cid] = name
                recording.clean_segments = existing
                flag_modified(recording, "clean_segments")
                await session.commit()
                logger.info(
                    f"[clean_orchestrator] saved clean for {recording_id} "
                    f"({len(existing['segments'])} segments)"
                )
            except Exception:
                await session.rollback()
                logger.exception(
                    f"[clean_orchestrator] failed for {recording_id}"
                )
    except Exception:
        logger.exception(
            f"[clean_orchestrator] outer error for {recording_id}"
        )
