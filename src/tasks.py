"""Celery tasks — long-running operations offloaded from FastAPI workers.

Pattern: each task is a @celery_app.task decorated function. Dispatched
via `.delay()` or `.apply_async()` from the API endpoint, runs in the
worker process pool (separate from FastAPI event loop).

Tasks expose state to the FE via Celery's result backend — FE polls
/api/tasks/{task_id} to check PENDING / STARTED / SUCCESS / FAILURE +
get the result when ready.

Imports happen INSIDE each task function so Celery's autodiscovery
doesn't pull SQLAlchemy / pyannote etc. at worker module load — keeps
worker startup fast even when tasks themselves are heavy.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from celery import states
from celery.exceptions import SoftTimeLimitExceeded

from meeting.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro_factory):
    """Run an async function inside a Celery task on a fresh event loop.

    Problem: SQLAlchemy's async engine holds a connection pool. asyncpg
    binds each connection to the event loop that created it. Celery's
    `solo` pool reuses a single Python process across task invocations
    — each task calls `asyncio.run(...)` which creates a NEW loop. The
    second task then tries to ping a pooled connection bound to the
    PREVIOUS loop and asyncpg raises "got Future attached to a different
    loop".

    Fix: dispose the engine pool at the start of every task so connections
    are re-created lazily on the current loop. Cheap — no live queries
    open between tasks, just TCP handshakes to Postgres on first checkout.

    Pass a coroutine FACTORY (zero-arg callable returning a coroutine)
    rather than a coroutine, so we can build the actual coro inside the
    new loop — the engine.dispose call has to happen before any
    connection is requested.
    """
    async def _wrapped():
        # Nuke every module-level async resource cached by previous tasks —
        # they were created in a now-dead event loop and are unusable here.
        # Each resource has the same "close needs original loop" trap, so
        # we use reference-drop semantics (no await close) for both.
        from meeting.db.base import async_engine
        await async_engine.dispose(close=False)
        try:
            from meeting.graphs.checkpointer import reset_checkpointer
            reset_checkpointer()
        except Exception:
            pass  # checkpointer module is optional for tasks that don't use it
        return await coro_factory()

    return asyncio.run(_wrapped())


@celery_app.task(
    bind=True,
    name="mee.gen_mom",
    autoretry_for=(ConnectionError,),
    retry_backoff=10,        # exponential backoff: 10s, 20s, 40s, ...
    retry_backoff_max=300,   # cap at 5 min between retries
    retry_jitter=True,
    max_retries=3,
)
def gen_mom_task(self, recording_id: str, ui_lang: str = "vi") -> dict:
    """Generate per-recording MoM via the LangGraph pipeline.

    State transitions visible to FE poller:
      PENDING  → task queued, no worker picked it up yet
      STARTED  → worker began executing (after task_track_started=True)
      SUCCESS  → returns {recording_id, notes, saved_paths}
      FAILURE  → returns {error: "..."}; FE shows error banner

    Auto-retries on ConnectionError (transient MaaS network blip). Retries
    use exponential backoff to avoid hammering the broker / LLM API.
    """
    logger.info(
        f"[gen_mom_task] recording_id={recording_id} ui_lang={ui_lang} "
        f"task_id={self.request.id}"
    )
    # Heavy imports inside task — autodiscovery doesn't load these at module init.
    from meeting.db.base import AsyncSessionLocal
    from meeting.graphs import run_mom_graph
    from meeting.graphs.checkpointer import init_checkpointer, get_checkpointer
    import os

    output_dir = os.getenv("OUTPUT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "output"
    )

    async def _run() -> dict:
        # Celery worker is a SEPARATE process from FastAPI — the app's
        # lifespan-startup hook (which init_checkpointer() in FastAPI) never
        # runs here. Lazy-init on first task call; init_checkpointer is
        # idempotent so concurrent tasks don't double-create the pool.
        try:
            checkpointer = get_checkpointer()
        except RuntimeError:
            checkpointer = await init_checkpointer()
        async with AsyncSessionLocal() as session:
            result = await run_mom_graph(
                recording_id=recording_id,
                session=session,
                output_dir=output_dir,
                checkpointer=checkpointer,
                mom_language=ui_lang,
            )
            # save_recording_mom only flush()es; run_mom_graph never commits.
            # Without this, recording.mom_json is rolled back on session close
            # (memory + .md persist via their own sessions, so the graph still
            # reports db:True — masking the loss). Same fix as _run_inline_mom.
            await session.commit()
            return result

    try:
        # Celery worker is sync — run the async graph in a fresh event loop.
        # _run_async also disposes the SQLAlchemy engine first so we don't
        # reuse asyncpg connections from a previous task's loop.
        final_state = _run_async(_run)
    except SoftTimeLimitExceeded:
        logger.warning(f"[gen_mom_task] soft time limit hit for {recording_id}")
        return {"error": "MoM generation timed out (>12 min)"}
    except Exception as e:
        logger.exception(f"[gen_mom_task] failed for {recording_id}")
        # Re-raise to mark task FAILURE; Celery captures exception in result.
        raise

    if final_state.get("error") and not final_state.get("mom_json"):
        return {"error": final_state["error"], "recording_id": recording_id}

    return {
        "recording_id": recording_id,
        "meeting_id": final_state.get("meeting_id"),
        "notes": final_state.get("mom_json", {}),
        "saved_paths": final_state.get("saved_paths", {}),
        "memory_context_count": len(final_state.get("memory_context", [])),
    }


@celery_app.task(
    bind=True,
    name="mee.diarize_recording",
    autoretry_for=(ConnectionError,),
    retry_backoff=10,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,
    soft_time_limit=60 * 60,    # 60 min soft cap — long meetings on CPU
    time_limit=75 * 60,
)
def diarize_recording_task(self, recording_id: str, audio_rel_path: str) -> dict:
    """Run pyannote on a staged WAV + write speaker results back to DB.

    Used by the chunked /api/transcribe path: Whisper returns text in
    ~2 min, the full audio is staged to disk, and this task takes the
    long 30-60 min CPU run separately. FE polls /api/tasks/{task_id};
    when state == SUCCESS the recording's speaker_embeddings +
    speaker_sample_paths + diarized_text are filled in and the SpeakerMapper
    UI lights up on next reload.

    Args:
      recording_id: target recording (already created by import-transcript)
      audio_rel_path: path RELATIVE to OUTPUT_DIR — resolved here to absolute.
    """
    logger.info(
        f"[diarize_recording_task] recording_id={recording_id} "
        f"audio={audio_rel_path} task_id={self.request.id}"
    )
    import os
    import base64

    from meeting.db.base import SyncSessionLocal
    from meeting.db import repositories_sync as repo_sync
    from meeting.services.local_diarize import (
        diarize_audio, split_text_proportional,
    )

    output_dir = os.getenv("OUTPUT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "output"
    )
    audio_path = (
        audio_rel_path
        if os.path.isabs(audio_rel_path)
        else os.path.join(output_dir, audio_rel_path)
    )
    if not os.path.exists(audio_path):
        logger.error(f"[diarize_recording_task] audio missing: {audio_path}")
        return {"error": f"audio missing: {audio_rel_path}"}

    try:
        with open(audio_path, "rb") as f:
            wav_bytes = f.read()
        size_mb = len(wav_bytes) // 1024 // 1024

        # Estimate audio duration from file size: PCM16 mono 16kHz = 32KB/s.
        # Above 15 min → use parallel chunked diarize for 2-4× speedup.
        # Below that, single-shot is faster (no chunking overhead + AHC merge).
        est_duration_s = len(wav_bytes) / 32_000  # rough but good enough
        if est_duration_s > 15 * 60:
            from meeting.services.parallel_diarize import diarize_parallel
            logger.info(
                f"[diarize_recording_task] loaded {size_mb}MB audio "
                f"(~{est_duration_s/60:.0f} min) → PARALLEL diarize "
                f"(15-min slices)"
            )
            result = diarize_parallel(wav_bytes, slice_seconds=15 * 60)
        else:
            logger.info(
                f"[diarize_recording_task] loaded {size_mb}MB audio "
                f"(~{est_duration_s/60:.0f} min) → single-shot diarize"
            )
            result = diarize_audio(wav_bytes)
    except SoftTimeLimitExceeded:
        logger.warning(f"[diarize_recording_task] soft time limit hit for {recording_id}")
        return {"error": "Diarization timed out (>60 min)"}
    except Exception as e:
        logger.exception(f"[diarize_recording_task] diarize failed: {e}")
        raise

    turns = result.get("turns") or []
    cluster_embeddings = result.get("cluster_embeddings") or {}
    sample_b64 = result.get("sample_audio_b64") or {}
    if not turns or not cluster_embeddings:
        logger.warning(
            f"[diarize_recording_task] pyannote returned no turns/clusters — "
            f"keeping recording as text-only"
        )
        # Still clean up the staged audio.
        try:
            os.unlink(audio_path)
        except OSError:
            pass
        return {"recording_id": recording_id, "clusters": 0, "samples": 0}

    # Write sample WAVs to output/<rid>/spk_<label>.wav so the same
    # GET /speaker-sample/{label} endpoint can serve them.
    rec_dir = os.path.join(output_dir, recording_id)
    os.makedirs(rec_dir, exist_ok=True)
    sample_paths: dict[str, str] = {}
    for label, b64 in sample_b64.items():
        try:
            data = base64.b64decode(b64)
            fpath = os.path.join(rec_dir, f"spk_{label}.wav")
            with open(fpath, "wb") as f:
                f.write(data)
            sample_paths[label] = os.path.relpath(fpath, output_dir)
        except Exception as e:
            logger.warning(
                f"[diarize_recording_task] sample write failed for {label}: {e}"
            )

    # Sync DB I/O — no event loop, no async pool. Same logic as before.
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified
    from meeting.db.models import TranscriptSegment

    with SyncSessionLocal() as session:
        recording = repo_sync.get_recording(session, uuid.UUID(recording_id))
        if not recording:
            logger.error(f"[diarize_recording_task] recording {recording_id} not found")
            return {"error": "recording not found"}
        recording.speaker_embeddings = cluster_embeddings
        flag_modified(recording, "speaker_embeddings")
        if sample_paths:
            recording.speaker_sample_paths = sample_paths
            flag_modified(recording, "speaker_sample_paths")
        # Re-split existing transcript proportionally to pyannote turns →
        # write SPEAKER_NN-tagged version. Cleaner LLM picks this up next run.
        existing_text = recording.diarized_text or ""
        if not existing_text:
            rows = session.execute(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.recording_id == recording.id,
                    TranscriptSegment.is_deleted.is_(False),
                )
                .order_by(TranscriptSegment.seq)
            ).scalars().all()
            existing_text = " ".join(
                (r.original_text or "").strip() for r in rows
            ).strip()
        if existing_text:
            split = split_text_proportional(existing_text, turns)
            lines, cur_spk, cur_buf = [], None, []
            for s in split:
                spk = (s.get("speaker") or "Unknown").strip()
                txt = (s.get("text") or "").strip()
                if not txt:
                    continue
                if spk == cur_spk:
                    cur_buf.append(txt)
                else:
                    if cur_buf and cur_spk:
                        lines.append(f"{cur_spk}: {' '.join(cur_buf)}")
                    cur_spk = spk
                    cur_buf = [txt]
            if cur_buf and cur_spk:
                lines.append(f"{cur_spk}: {' '.join(cur_buf)}")
            if lines:
                recording.diarized_text = "\n\n".join(lines)
        # Force cleaner to re-run with the new speaker tags on next /clean call.
        recording.clean_segments = None
        session.commit()

    # Drop the staged WAV — we have everything we need in DB + on disk now.
    try:
        os.unlink(audio_path)
    except OSError:
        pass

    return {
        "recording_id": recording_id,
        "clusters": len(cluster_embeddings),
        "samples": len(sample_paths),
        "turns": len(turns),
    }


@celery_app.task(
    bind=True,
    name="mee.clean_recording",
    autoretry_for=(ConnectionError,),
    retry_backoff=10,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=2,
    # MaaS LLM gateway typically times out around 60-120s; the cleaner
    # itself can take 2-4 min on long transcripts. Soft cap at 12 min
    # gives room for cascading temperature retries + JSON repair.
    soft_time_limit=12 * 60,
    time_limit=15 * 60,
)
def clean_recording_task(self, recording_id: str, regenerate: bool = False) -> dict:
    """Run the cleaner LLM pass on a recording in the background.

    Mirrors the inline /clean endpoint logic but runs in the Celery worker
    so the HTTP request returns immediately with a task_id. FE polls
    /api/tasks/{task_id}; on SUCCESS it re-calls /clean and gets the
    cached result instantly.

    State transitions visible to FE:
      PENDING  → queued, no worker yet
      STARTED  → cleaner is running
      SUCCESS  → returns {recording_id, segments_count, cluster_mapping_size}
      FAILURE  → returns {error: "..."}, FE shows banner
    """
    logger.info(
        f"[clean_recording_task] recording_id={recording_id} "
        f"regenerate={regenerate} task_id={self.request.id}"
    )

    from meeting.db.base import SyncSessionLocal
    from meeting.db import repositories_sync as repo_sync
    from meeting.services.transcript_cleaner import clean_transcript
    from meeting.services.model_registry import resolve_llm
    from meeting.services.phonetic_generator import (
        generate_phonetic_mappings, needs_regeneration,
    )
    from sqlalchemy.orm.attributes import flag_modified

    try:
        with SyncSessionLocal() as session:
            rid = uuid.UUID(recording_id)
            recording = repo_sync.get_recording(session, rid)
            if not recording:
                return {"error": "Recording not found"}

            # Pick source — same priority as inline /clean.
            raw_text = (recording.diarized_text or "").strip()
            if not raw_text:
                raw_text = repo_sync.join_recording_transcript(session, rid)
            if not raw_text or not raw_text.strip():
                return {"error": "No transcript segments to clean"}

            meeting = repo_sync.get_meeting(session, recording.meeting_id)
            attendees_str = ""
            if recording.attendees:
                attendees_str = ", ".join(
                    a.get("name", "")
                    for a in recording.attendees if isinstance(a, dict)
                )

            pre_mapped: dict[str, str] = {}
            if recording.speaker_embeddings:
                user = repo_sync.get_or_create_dev_user(session)
                pre_mapped = repo_sync.match_clusters_to_names_sync(
                    session,
                    user_id=user.id,
                    speaker_embeddings=recording.speaker_embeddings,
                )

            vocab_parts = [
                (meeting.vocab_hints or "").strip() if meeting else "",
                (recording.vocab_hints or "").strip(),
            ]
            merged_vocab = ", ".join(p for p in vocab_parts if p) or None

            llm_profile = resolve_llm(
                recording_choice=recording.llm_model,
                meeting_choice=getattr(meeting, "llm_model", None) if meeting else None,
            )
            logger.info(
                f"[clean_recording_task] {recording_id} LLM="
                f"{llm_profile.get('id')} ({llm_profile.get('model')})"
            )

            phonetic_mappings: list[dict] = []
            if merged_vocab:
                cached_phon = recording.phonetic_examples_json or {}
                if needs_regeneration(merged_vocab, cached_phon):
                    new_phon = generate_phonetic_mappings(
                        merged_vocab, llm_profile=llm_profile,
                    )
                    repo_sync.save_recording_phonetic(session, rid, new_phon)
                    phonetic_mappings = new_phon.get("mappings", [])
                else:
                    phonetic_mappings = cached_phon.get("mappings", [])

            # Heavy LLM call — sync OpenAI SDK, no need for to_thread now
            # that the surrounding function is itself sync.
            result = clean_transcript(
                raw_text=raw_text,
                attendees=attendees_str,
                pre_mapped=pre_mapped or None,
                vocab_hints=merged_vocab,
                phonetic_examples=phonetic_mappings or None,
                llm_profile=llm_profile,
            )
            if "error" in result:
                return {"error": result["error"]}

            segs = result.get("segments", [])
            if not segs:
                return {
                    "error": (
                        "Cleaner produced 0 segments — likely LLM rate limit "
                        "or upstream timeout. Try again later or switch model."
                    )
                }

            existing = recording.clean_segments or {}
            existing["segments"] = segs
            existing["cluster_mapping"] = result.get("cluster_mapping", {})
            for cid, name in pre_mapped.items():
                existing["cluster_mapping"][cid] = name
            recording.clean_segments = existing
            flag_modified(recording, "clean_segments")
            session.commit()
            return {
                "recording_id": recording_id,
                "segments_count": len(segs),
                "cluster_mapping_size": len(existing["cluster_mapping"]),
            }
    except SoftTimeLimitExceeded:
        logger.warning(f"[clean_recording_task] soft time limit for {recording_id}")
        return {"error": "Cleaner timed out (>12 min)"}
    except Exception as e:
        logger.exception(f"[clean_recording_task] failed: {e}")
        raise


def get_task_state(task_id: str) -> dict:
    """Read current state of a task. Called by the /api/tasks/{id} endpoint
    so FE can poll without importing celery directly."""
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    state = result.state  # PENDING / STARTED / SUCCESS / FAILURE / RETRY / REVOKED
    payload: dict = {"task_id": task_id, "state": state}

    if state == states.SUCCESS:
        payload["result"] = result.result
    elif state == states.FAILURE:
        # result here is the exception; stringify for JSON
        exc = result.result
        payload["error"] = str(exc) if exc else "unknown error"
    elif state == states.RETRY:
        payload["error"] = str(result.result) if result.result else "retrying"
    # PENDING + STARTED: no extra fields; FE keeps polling.

    return payload
