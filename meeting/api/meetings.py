"""
Meetings API — DB-backed endpoints (Phase A).

Routes:
    POST   /api/meetings                 → create meeting (auto owner membership)
    GET    /api/meetings                 → list meetings for current user
    GET    /api/meetings/{id}            → meeting detail + recordings + segments
    POST   /api/meetings/{id}/recordings → start a new recording
    POST   /api/recordings/{id}/end      → end recording
    POST   /api/recordings/{id}/segments → append a segment
    POST   /api/meetings/{id}/generate-mom → run MoM gen against DB transcript

Auth: until M365 is wired (Phase E), every request uses a dev_user.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import os

from meeting.db import get_session
from meeting.db import repositories as repo
from meeting.graphs import get_checkpointer, run_mom_graph
from meeting.note_generator import generate_meeting_notes
from meeting.services import clean_transcript

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["meetings"])


# ─── Schemas ──────────────────────────────────────────────────────

class MeetingCreate(BaseModel):
    """Create a project. Only project-level fields; per-meeting metadata is
    added later via RecordingPatch as recordings are created."""
    title: str = "Untitled meeting"
    vocab_hints: Optional[str] = None


class MeetingOut(BaseModel):
    """Project-level fields only. Per-meeting-event metadata lives on
    Recording (see RecordingOut)."""
    id: str
    title: str
    vocab_hints: Optional[str] = None
    status: str
    has_summary: bool = False
    is_pinned: bool = False
    stt_model: Optional[str] = None
    llm_model: Optional[str] = None
    mom_language: Optional[str] = None


class MeetingPatch(BaseModel):
    """Project-level patch. Per-recording metadata uses RecordingPatch."""
    title: Optional[str] = None
    is_pinned: Optional[bool] = None
    vocab_hints: Optional[str] = None
    # Project default model picks (logical IDs from model_registry). Empty
    # string clears back to NULL = inherit registry default.
    stt_model: Optional[str] = None
    llm_model: Optional[str] = None
    # MoM generation language ("vi" / "en"). NULL = inherit UI lang at gen time.
    mom_language: Optional[str] = None


class SegmentCreate(BaseModel):
    seq: int
    original_text: str
    start_time_ms: Optional[int] = None
    end_time_ms: Optional[int] = None
    speaker: Optional[str] = None


class RecordingCreate(BaseModel):
    session_label: Optional[str] = None


class TranscriptSegmentIn(BaseModel):
    """One segment from PhoWhisper response. start/end in seconds (float).
    Backend converts to ms when saving."""
    text: str
    speaker: Optional[str] = None
    start: Optional[float] = None  # seconds
    end: Optional[float] = None    # seconds


class TranscriptImport(BaseModel):
    text: str
    # Optional structured segments — when present, takes precedence over
    # splitting `text`. Lets FE preserve speaker tags + timestamps from
    # PhoWhisper diarization (file upload path).
    segments: Optional[list[TranscriptSegmentIn]] = None
    # Default None — only override the recording's session_label if caller
    # explicitly passes a value. Previously defaulted to "Imported transcript"
    # which silently renamed recordings created with a meaningful label.
    session_label: Optional[str] = None
    replace: bool = True  # True = xoá recordings cũ trước khi import (chỉ áp khi recording_id=None)
    duration_sec: Optional[int] = None  # caller can pass actual duration (live record, audio file)
    recording_id: Optional[str] = None  # target an EXISTING recording — overwrites its segments
    # 256-dim per-cluster voice embeddings from PhoWhisper server (optional —
    # VNG MaaS Whisper doesn't produce these). Stored on recording.speaker_embeddings
    # for the Clean step's speaker matcher.
    cluster_embeddings: Optional[dict] = None
    # Base64-encoded 3s WAV per cluster — same shape as /diarize-result.
    # Persisted to disk + recording.speaker_sample_paths so SpeakerMapper can
    # play a sample per cluster before the user confirms the name.
    sample_audio_b64: Optional[dict[str, str]] = None
    # Chunked upload path: full WAV staged to output/_pending_diarize/*.wav.
    # When set, backend dispatches Celery diarize_recording_task to compute
    # speaker turns + embeddings + sample clips asynchronously (CPU pyannote
    # on hour-long files = 30-60 min). Returned task_id lets FE poll.
    pending_diarize_path: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────

def _meeting_to_out(m) -> MeetingOut:
    return MeetingOut(
        id=str(m.id),
        title=m.title,
        vocab_hints=getattr(m, "vocab_hints", None),
        status=m.status,
        has_summary=getattr(m, "project_summary_json", None) is not None,
        is_pinned=getattr(m, "is_pinned", False),
        stt_model=getattr(m, "stt_model", None),
        llm_model=getattr(m, "llm_model", None),
        mom_language=getattr(m, "mom_language", None),
    )


def _parse_uuid(s: str) -> uuid.UUID:
    try:
        return uuid.UUID(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {s}")


# ─── Endpoints ────────────────────────────────────────────────────


@router.get("/models")
async def list_available_models():
    """List STT + LLM profiles for the model-picker dropdown in the UI.
    Each entry has id, label, description. Defaults indicate the fallback
    when neither recording nor meeting has a value set."""
    from meeting.services.model_registry import (
        get_profiles, DEFAULT_STT, DEFAULT_LLM,
    )
    return {
        "stt": get_profiles("stt"),
        "llm": get_profiles("llm"),
        "default_stt": DEFAULT_STT,
        "default_llm": DEFAULT_LLM,
    }


@router.post("/meetings", response_model=MeetingOut)
async def create_meeting_endpoint(
    req: MeetingCreate, session: AsyncSession = Depends(get_session)
):
    user = await repo.get_or_create_dev_user(session)
    meeting = await repo.create_meeting(
        session,
        user_id=user.id,
        title=req.title,
        vocab_hints=req.vocab_hints,
    )
    return _meeting_to_out(meeting)


@router.get("/meetings", response_model=list[MeetingOut])
async def list_meetings_endpoint(session: AsyncSession = Depends(get_session)):
    user = await repo.get_or_create_dev_user(session)
    meetings = await repo.list_meetings_for_user(session, user.id)
    return [_meeting_to_out(m) for m in meetings]


@router.get("/meetings/{meeting_id}")
async def get_meeting_endpoint(
    meeting_id: str, session: AsyncSession = Depends(get_session)
):
    mid = _parse_uuid(meeting_id)
    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {
        **_meeting_to_out(meeting).model_dump(),
        "recordings": [
            {
                "id": str(r.id),
                "session_label": r.session_label,
                "title": r.title,
                "purpose": r.purpose,
                "date": r.date.isoformat() if r.date else None,
                "venue": r.venue,
                "chaired_by": r.chaired_by,
                "noted_by": r.noted_by,
                "attendees": r.attendees,
                "vocab_hints": r.vocab_hints,
                "stt_model": r.stt_model,
                "llm_model": r.llm_model,
                "mom_language": r.mom_language,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "duration_sec": r.duration_sec,
                "status": r.status,
                "segment_count": len([s for s in r.segments if not s.is_deleted]),
                "mom_json": r.mom_json,
                "has_clean": r.clean_segments is not None,
                "speaker_samples": list((r.speaker_sample_paths or {}).keys()),
            }
            for r in meeting.recordings
        ],
        "project_summary_json": meeting.project_summary_json,
    }


@router.patch("/meetings/{meeting_id}", response_model=MeetingOut)
async def patch_meeting_endpoint(
    meeting_id: str,
    req: MeetingPatch,
    session: AsyncSession = Depends(get_session),
):
    """Update project-level metadata. Per-meeting-event metadata uses
    PATCH /recordings/{id}/metadata instead."""
    mid = _parse_uuid(meeting_id)
    updated = await repo.update_meeting(
        session, mid,
        title=req.title,
        is_pinned=req.is_pinned,
        vocab_hints=req.vocab_hints,
        stt_model=req.stt_model,
        llm_model=req.llm_model,
        mom_language=req.mom_language,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return _meeting_to_out(updated)


@router.delete("/meetings/{meeting_id}")
async def delete_meeting_endpoint(
    meeting_id: str, session: AsyncSession = Depends(get_session)
):
    """Soft-delete a meeting (sets deleted_at; recordings/segments stay for audit)."""
    mid = _parse_uuid(meeting_id)
    ok = await repo.soft_delete_meeting(session, mid)
    if not ok:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {"meeting_id": meeting_id, "deleted": True}


@router.get("/meetings/{meeting_id}/download")
async def download_meeting_summary(
    meeting_id: str,
    fmt: str = "md",
    session: AsyncSession = Depends(get_session),
):
    """Download project summary (tổng kết project) as Markdown or JSON.

    For per-recording MoM, use /api/recordings/{id}/download instead.
    """
    from fastapi.responses import FileResponse, JSONResponse
    from meeting.report_generator import generate_mom_markdown
    import os as _os

    mid = _parse_uuid(meeting_id)
    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.project_summary_json:
        raise HTTPException(
            status_code=400,
            detail="Tổng kết project chưa được tạo. Generate qua POST /api/meetings/{id}/generate-project-summary",
        )

    if fmt == "json":
        return JSONResponse(content=meeting.project_summary_json)

    out_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "output")
    md_path = generate_mom_markdown(
        notes=meeting.project_summary_json,
        output_dir=out_dir,
        recording_label=f"{meeting.title}-summary",
    )
    return FileResponse(
        md_path,
        media_type="text/markdown",
        filename=_os.path.basename(md_path),
    )


@router.get("/meetings/{meeting_id}/transcript")
async def get_meeting_transcript_endpoint(
    meeting_id: str, session: AsyncSession = Depends(get_session)
):
    """Return joined raw transcript text of all segments in this meeting."""
    mid = _parse_uuid(meeting_id)
    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    text = await repo.join_meeting_transcript(session, mid)
    return {"meeting_id": meeting_id, "transcript": text}


@router.post("/meetings/{meeting_id}/recordings")
async def start_recording_endpoint(
    meeting_id: str,
    req: RecordingCreate,
    session: AsyncSession = Depends(get_session),
):
    mid = _parse_uuid(meeting_id)
    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    recording = await repo.start_recording(
        session, meeting_id=mid, session_label=req.session_label
    )
    return {
        "id": str(recording.id),
        "meeting_id": meeting_id,
        "status": recording.status,
        "started_at": recording.started_at.isoformat(),
    }


@router.get("/recordings/{recording_id}/transcript")
async def get_recording_transcript_endpoint(
    recording_id: str, session: AsyncSession = Depends(get_session)
):
    """Return joined raw transcript text + meta (segment_count, duration_sec) of 1 recording."""
    from meeting.db.models import TranscriptSegment
    from sqlalchemy import select, func

    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    text = await repo.join_recording_transcript(session, rid)
    # Fetch full segments (with speaker + timestamps) for rich Raw view rendering.
    seg_stmt = (
        select(TranscriptSegment)
        .where(
            TranscriptSegment.recording_id == rid,
            TranscriptSegment.is_deleted.is_(False),
        )
        .order_by(TranscriptSegment.seq)
    )
    db_segs = (await session.execute(seg_stmt)).scalars().all()
    segments_out = [
        {
            "seq": s.seq,
            "text": s.text,
            "speaker": s.speaker,
            "start_ms": s.start_time_ms,
            "end_ms": s.end_time_ms,
        }
        for s in db_segs
    ]
    return {
        "recording_id": recording_id,
        "meeting_id": str(recording.meeting_id),
        "session_label": recording.session_label,
        "transcript": text,
        "segments": segments_out,
        "segment_count": len(db_segs),
        "duration_sec": recording.duration_sec,
        "started_at": recording.started_at.isoformat() if recording.started_at else None,
        "ended_at": recording.ended_at.isoformat() if recording.ended_at else None,
    }


class RecordingPatch(BaseModel):
    """Per-recording metadata. session_label kept for backward-compat
    (sidebar fallback when title not set)."""
    session_label: Optional[str] = None
    title: Optional[str] = None
    purpose: Optional[str] = None
    date: Optional[date_type] = None
    venue: Optional[str] = None
    chaired_by: Optional[str] = None
    noted_by: Optional[str] = None
    attendees: Optional[list] = None
    vocab_hints: Optional[str] = None
    # Per-recording override (logical IDs). NULL = inherit from meeting.
    stt_model: Optional[str] = None
    llm_model: Optional[str] = None
    mom_language: Optional[str] = None


@router.patch("/recordings/{recording_id}")
async def patch_recording_endpoint(
    recording_id: str,
    req: RecordingPatch,
    session: AsyncSession = Depends(get_session),
):
    """Update per-recording metadata (any subset of fields)."""
    rid = _parse_uuid(recording_id)
    # Snapshot vocab_hints BEFORE update so we can invalidate the phonetic
    # cache if the user changed it. update_recording_metadata trims to None
    # internally; do the same here so the comparison matches its semantics.
    pre = await repo.get_recording(session, rid)
    prev_vocab = (pre.vocab_hints or "").strip() if pre else ""
    next_vocab = (
        (req.vocab_hints or "").strip() if req.vocab_hints is not None else prev_vocab
    )
    updated = await repo.update_recording_metadata(
        session, rid,
        session_label=req.session_label,
        title=req.title,
        purpose=req.purpose,
        date=req.date,
        venue=req.venue,
        chaired_by=req.chaired_by,
        noted_by=req.noted_by,
        attendees=req.attendees,
        vocab_hints=req.vocab_hints,
        stt_model=req.stt_model,
        llm_model=req.llm_model,
        mom_language=req.mom_language,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Recording not found")
    # Propagate model pick + vocab to the parent meeting as the "latest project
    # default" — new sibling recordings inherit from meeting when they have no
    # own value. Lets user pick a model once on Recording 1 and it sticks for
    # every Recording 2/3/4 in the same project without having to re-pick.
    propagate: dict = {}
    if req.stt_model is not None:
        propagate["stt_model"] = req.stt_model
    if req.llm_model is not None:
        propagate["llm_model"] = req.llm_model
    if req.mom_language is not None:
        propagate["mom_language"] = req.mom_language
    if req.vocab_hints is not None:
        # Only propagate when the user EXPLICITLY edited vocab on this recording
        # AND the meeting doesn't already have a project default — otherwise we
        # could clobber a deliberately separate project-level vocab.
        meeting_now = await repo.get_meeting(session, updated.meeting_id)
        if meeting_now and not (meeting_now.vocab_hints or "").strip():
            propagate["vocab_hints"] = req.vocab_hints
    if propagate:
        await repo.update_meeting(session, updated.meeting_id, **propagate)
    # Drop phonetic cache when vocab changed — next /clean call regenerates.
    if req.vocab_hints is not None and next_vocab != prev_vocab:
        updated.phonetic_examples_json = None
    return {
        "recording_id": str(updated.id),
        "session_label": updated.session_label,
        "title": updated.title,
    }


class DiarizeResult(BaseModel):
    """Per-cluster embeddings + diarized transcript produced by post-record
    diarization (live record path). The WebSocket server
    (whisper_live/backend/maas_backend.py) sends the full audio buffer to
    PhoWhisper after END_OF_AUDIO and POSTs the resulting cluster_embeddings
    + speaker-tagged transcript here so /clean + voiceprint enrollment work
    the same as the file-upload path.

    `diarized_text` (optional): formatted as "SPEAKER_00: text\\n\\nSPEAKER_01:
    text\\n…". When present, /clean prefers this over join_recording_transcript
    so the cleaner LLM receives speaker tags as ground truth.
    """

    cluster_embeddings: dict[str, list[float]]
    diarized_text: Optional[str] = None
    # Optional: base64-encoded 3s WAV per cluster, produced by local_diarize.
    # When present, backend decodes + writes to disk and stores paths so
    # SpeakerMapper can play a clip per speaker before confirming the name.
    sample_audio_b64: Optional[dict[str, str]] = None


@router.post("/recordings/{recording_id}/diarize-result")
async def diarize_result_endpoint(
    recording_id: str,
    req: DiarizeResult,
    session: AsyncSession = Depends(get_session),
):
    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    if not req.cluster_embeddings:
        return {"updated": False, "reason": "no embeddings"}
    recording.speaker_embeddings = req.cluster_embeddings
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(recording, "speaker_embeddings")
    if req.diarized_text and req.diarized_text.strip():
        recording.diarized_text = req.diarized_text

    # Decode + write per-speaker sample WAVs. Stored under output/<rid>/ so
    # the cleanup path is the same as the rest of the recording artifacts.
    if req.sample_audio_b64:
        import base64
        import os as _os
        output_dir = _os.getenv("OUTPUT_DIR") or _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
            "output",
        )
        rec_dir = _os.path.join(output_dir, recording_id)
        _os.makedirs(rec_dir, exist_ok=True)
        paths: dict[str, str] = {}
        for label, b64 in req.sample_audio_b64.items():
            try:
                data = base64.b64decode(b64)
                fname = f"spk_{label}.wav"
                fpath = _os.path.join(rec_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(data)
                # Store path RELATIVE to output_dir so moving the output
                # directory doesn't break lookup. Endpoint resolves back to
                # absolute via the same OUTPUT_DIR env at serve time.
                paths[label] = _os.path.relpath(fpath, output_dir)
            except Exception as e:
                import logging as _lg
                _lg.warning(
                    f"[diarize-result] failed to write sample for {label}: {e}"
                )
        if paths:
            recording.speaker_sample_paths = paths
            flag_modified(recording, "speaker_sample_paths")
    # NOTE: We deliberately DO NOT null out clean_segments here.
    # If user has already cleaned (possibly with manual SpeakerMapper edits),
    # wiping the cache would force a re-run on next click + lose user edits.
    # User can press "Regenerate" explicitly if they want fresh clean with
    # the new voice-match info — see /clean endpoint with regenerate=true.
    await session.flush()
    await session.commit()

    # If recording has no clean yet (live record path — segments were saved
    # before diarize finished), trigger background clean NOW that we have
    # both embeddings + diarized text. User clicks Clean tab → instant.
    if not recording.clean_segments:
        from meeting.services.clean_orchestrator import trigger_background
        trigger_background(recording_id)
    return {
        "updated": True,
        "recording_id": recording_id,
        "clusters": list(req.cluster_embeddings.keys()),
        "diarized_text_chars": len(req.diarized_text or ""),
    }


@router.get("/recordings/{recording_id}/speaker-sample/{label}")
async def speaker_sample_endpoint(
    recording_id: str,
    label: str,
    session: AsyncSession = Depends(get_session),
):
    """Serve a per-cluster 3s WAV. Used by SpeakerMapper play button so the
    user can verify the speaker → name mapping by ear before saving the
    voiceprint. 404 when the cluster has no stored sample (pre-0016
    recording, PhoWhisper path, or extraction failed at diarize time)."""
    import os as _os
    from fastapi.responses import FileResponse

    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    paths = recording.speaker_sample_paths or {}
    rel = paths.get(label)
    if not rel:
        raise HTTPException(status_code=404, detail="No sample for this cluster")
    output_dir = _os.getenv("OUTPUT_DIR") or _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
        "output",
    )
    fpath = rel if _os.path.isabs(rel) else _os.path.join(output_dir, rel)
    # Defensive: prevent path traversal — resolved path must stay under
    # output_dir even if recording.speaker_sample_paths got tampered with.
    if not _os.path.realpath(fpath).startswith(_os.path.realpath(output_dir)):
        raise HTTPException(status_code=400, detail="Invalid sample path")
    if not _os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="Sample file missing on disk")
    return FileResponse(fpath, media_type="audio/wav")


@router.get("/recordings/{recording_id}/clean-status")
async def clean_status_endpoint(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Lightweight poll endpoint for FE — tells whether clean is ready, running
    in background, or hasn't started. Used by Clean-tab progress indicator so
    user knows they can click Clean without waiting for LLM."""
    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    from meeting.services.clean_orchestrator import is_inflight, get_progress
    is_running = is_inflight(recording_id)
    has_clean = recording.clean_segments is not None and bool(
        recording.clean_segments
    )
    progress = get_progress(recording_id)

    if is_running:
        status = "running"
    elif has_clean:
        status = "done"
    else:
        status = "idle"

    return {
        "recording_id": recording_id,
        "status": status,
        "has_clean": has_clean,
        "in_flight": is_running,
        "progress": progress,  # {phase, current_chunk, total_chunks, started_at_ms, raw_chars} or null
    }


@router.post("/recordings/{recording_id}/clean")
async def clean_recording_endpoint(
    recording_id: str,
    regenerate: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """LLM clean transcript for 1 recording, cached per-recording in DB.

    First call runs the LLM and persists the result to `recordings.clean_segments`.
    Subsequent calls return the cached value to avoid re-billing the LLM. Pass
    `?regenerate=true` to force a fresh LLM run (overwrites the cache).
    """
    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    # Cluster ids with stored embeddings on this recording (set by PhoWhisper
    # at upload). Clusters NOT in this list cannot be saved as voiceprints —
    # the audio is gone unless user re-uploads.
    available_clusters = list((recording.speaker_embeddings or {}).keys())

    # Share in-flight background clean if any — avoid duplicate LLM call when
    # user clicks Clean before the post-import background task finishes.
    if not regenerate:
        from meeting.services.clean_orchestrator import (
            is_inflight, wait_for_inflight,
        )
        if is_inflight(recording_id):
            await wait_for_inflight(recording_id)
            # Re-fetch — background task may have populated clean_segments
            await session.refresh(recording)

    # Return cache hit unless caller forces regenerate
    # Check cache. Treat `{segments: []}` as miss — leftover from a previous
    # failed run (all chunks 429'd) and shouldn't trap user in empty Clean view.
    cached_segs = (recording.clean_segments or {}).get("segments") or []
    if not regenerate and recording.clean_segments and cached_segs:
        cached = recording.clean_segments or {}
        return {
            "recording_id": recording_id,
            "cached": True,
            "clean_segments": cached.get("segments", []),
            "cluster_mapping": cached.get("cluster_mapping", {}),
            "available_clusters": available_clusters,
            "edited_html": cached.get("edited_html"),
            "edited_text": cached.get("edited_text"),
        }

    # Cache miss (or forced regenerate). Dispatch to Celery so this HTTP
    # request returns IMMEDIATELY — the LLM call may take 2-4 min and
    # the MaaS gateway times out earlier with 504 anyway. FE polls
    # /api/tasks/{id}; on SUCCESS it re-calls /clean and hits the cache.
    try:
        from meeting.celery_app import is_broker_reachable
        if is_broker_reachable():
            from meeting.tasks import clean_recording_task
            ar = clean_recording_task.delay(recording_id, regenerate)
            logger.info(
                f"[/clean] dispatched clean_recording_task id={ar.id} for "
                f"recording={recording_id} regenerate={regenerate}"
            )
            return {
                "recording_id": recording_id,
                "cached": False,
                "task_id": ar.id,
                "status": "queued",
                "mode": "celery",
                "available_clusters": available_clusters,
            }
    except Exception as e:
        logger.warning(f"[/clean] dispatch failed, falling back local: {e}")

    # Fallback path — broker unreachable. Spawn an in-process asyncio task
    # so the HTTP request still returns IMMEDIATELY (UX parity with the
    # Celery path). Heavy LLM work runs in background; FE polls
    # /api/tasks/{local_task_id}. The endpoint registers state into
    # _local_task_state via clean_orchestrator.
    from meeting.services.clean_orchestrator import dispatch_local_task
    from meeting.services.transcript_cleaner import clean_transcript as _clean_fn
    from meeting.services.speaker_matcher import match_clusters_to_names
    from meeting.services.model_registry import resolve_llm
    from meeting.services.phonetic_generator import (
        generate_phonetic_mappings, needs_regeneration,
    )
    from sqlalchemy.orm.attributes import flag_modified

    # Capture parameters now — the coroutine runs later in a fresh session.
    target_rid_str = recording_id
    target_regenerate = regenerate

    async def _run_inline_clean():
        from meeting.db.base import AsyncSessionLocal
        async with AsyncSessionLocal() as s2:
            r = await repo.get_recording(s2, _parse_uuid(target_rid_str))
            if not r:
                return {"error": "recording not found"}
            raw = (r.diarized_text or "").strip()
            if not raw:
                raw = await repo.join_recording_transcript(
                    s2, _parse_uuid(target_rid_str),
                )
            if not raw.strip():
                return {"error": "no transcript"}
            m = await repo.get_meeting(s2, r.meeting_id)
            attendees = ""
            if r.attendees:
                attendees = ", ".join(
                    a.get("name", "") for a in r.attendees if isinstance(a, dict)
                )
            pm: dict[str, str] = {}
            if r.speaker_embeddings:
                u = await repo.get_or_create_dev_user(s2)
                pm = await match_clusters_to_names(
                    s2, user_id=u.id, speaker_embeddings=r.speaker_embeddings,
                )
            vocab_parts = [
                (m.vocab_hints or "").strip() if m else "",
                (r.vocab_hints or "").strip(),
            ]
            merged = ", ".join(p for p in vocab_parts if p) or None
            llmp = resolve_llm(
                recording_choice=r.llm_model,
                meeting_choice=getattr(m, "llm_model", None) if m else None,
            )
            phons: list[dict] = []
            if merged:
                cached = r.phonetic_examples_json or {}
                if needs_regeneration(merged, cached):
                    import asyncio as _aio2
                    np_data = await _aio2.to_thread(
                        generate_phonetic_mappings, merged, llm_profile=llmp,
                    )
                    await repo.save_recording_phonetic(
                        s2, _parse_uuid(target_rid_str), np_data,
                    )
                    phons = np_data.get("mappings", [])
                else:
                    phons = cached.get("mappings", [])
            import asyncio as _aio3
            result = await _aio3.to_thread(
                _clean_fn,
                raw_text=raw, attendees=attendees, pre_mapped=pm or None,
                vocab_hints=merged, phonetic_examples=phons or None,
                llm_profile=llmp,
            )
            if "error" in result or not result.get("segments"):
                return {"error": result.get("error") or "cleaner produced 0 segments"}
            existing = r.clean_segments or {}
            existing["segments"] = result["segments"]
            existing["cluster_mapping"] = result.get("cluster_mapping", {})
            for cid, name in pm.items():
                existing["cluster_mapping"][cid] = name
            r.clean_segments = existing
            flag_modified(r, "clean_segments")
            await s2.commit()
            return {
                "recording_id": target_rid_str,
                "segments_count": len(result["segments"]),
            }

    local_task_id = dispatch_local_task(_run_inline_clean, prefix="clean-local")
    logger.info(
        f"[/clean] dispatched LOCAL asyncio task id={local_task_id} for "
        f"recording={recording_id} regenerate={regenerate}"
    )
    return {
        "recording_id": recording_id,
        "cached": False,
        "task_id": local_task_id,
        "status": "queued",
        "mode": "asyncio",
        "available_clusters": available_clusters,
    }

    # Note: the synchronous inline path below is now unreachable for the
    # cache-miss case. Kept temporarily for reference; will be removed
    # after this fallback is verified stable.
    logger.info(f"[/clean] running inline (broker unreachable) for {recording_id}")

    # Source priority for the cleaner LLM input:
    #   1. recording.diarized_text — PhoWhisper-tagged "SPEAKER_NN: text" format
    #      (live record + future file-upload path). Already has speaker tags,
    #      cleaner uses them as ground truth instead of guessing.
    #   2. join_recording_transcript() — untagged text from transcript_segments
    #      (legacy + MaaS live streaming path).
    if recording.diarized_text and recording.diarized_text.strip():
        raw_text = recording.diarized_text
        logger.info(
            f"[/clean] using diarized_text ({len(raw_text)} chars) for {recording_id}"
        )
    else:
        raw_text = await repo.join_recording_transcript(session, rid)
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No segments to clean")

    meeting = await repo.get_meeting(session, recording.meeting_id)
    # Per-meeting-event attendees live on recording now (project-level
    # attendees was removed in migration 0012).
    attendees_str = ""
    if recording.attendees:
        attendees_str = ", ".join(
            a.get("name", "") for a in recording.attendees if isinstance(a, dict)
        )

    # ─── Voice match clusters against the user's voiceprints DB ───
    # Recognises returning speakers across meetings (Approach D of speaker ID).
    pre_mapped: dict[str, str] = {}
    if recording.speaker_embeddings:
        from meeting.services.speaker_matcher import match_clusters_to_names
        user = await repo.get_or_create_dev_user(session)
        pre_mapped = await match_clusters_to_names(
            session,
            user_id=user.id,
            speaker_embeddings=recording.speaker_embeddings,
        )

    # Vocab is 2-tier: meeting (project default) + recording (session-specific
    # additions). Append both so cleaner sees full vocab. Empty parts dropped.
    vocab_parts = [
        (meeting.vocab_hints or "").strip() if meeting else "",
        (recording.vocab_hints or "").strip(),
    ]
    merged_vocab = ", ".join(p for p in vocab_parts if p) or None

    # Resolve effective LLM profile (recording → meeting → registry default).
    from meeting.services.model_registry import resolve_llm
    llm_profile = resolve_llm(
        recording_choice=recording.llm_model,
        meeting_choice=getattr(meeting, "llm_model", None) if meeting else None,
    )
    logger.info(
        f"[/clean] {recording_id} using LLM={llm_profile.get('id')} "
        f"({llm_profile.get('model')})"
    )

    # Dynamic phonetic few-shot for the cleaner — 1 LLM call to translate vocab
    # into VN phonetic variants ("convolution" → "công vô lu sần"). Cached on
    # recording so subsequent /clean reuses; regenerated only if vocab hash
    # changes. Falls back gracefully to the prompt's built-in pattern examples
    # if generation fails.
    phonetic_mappings: list[dict] = []
    if merged_vocab:
        import asyncio as _aio
        from meeting.services.phonetic_generator import (
            generate_phonetic_mappings, needs_regeneration,
        )
        cached_phon = recording.phonetic_examples_json or {}
        if needs_regeneration(merged_vocab, cached_phon):
            logger.info(
                f"[/clean] regenerating phonetic mappings for {recording_id} "
                f"(vocab changed)"
            )
            new_phon = await _aio.to_thread(
                generate_phonetic_mappings, merged_vocab,
                llm_profile=llm_profile,
            )
            await repo.save_recording_phonetic(session, rid, new_phon)
            phonetic_mappings = new_phon.get("mappings", [])
        else:
            phonetic_mappings = cached_phon.get("mappings", [])

    # Same threading rationale as mom_graph.generate_mom: clean_transcript
    # uses sync OpenAI SDK → wrap in to_thread so it doesn't block the event
    # loop for 2-3 min while every other request piles up pending.
    import asyncio as _aio_clean
    result = await _aio_clean.to_thread(
        clean_transcript,
        raw_text=raw_text,
        attendees=attendees_str,
        pre_mapped=pre_mapped or None,
        vocab_hints=merged_vocab,
        phonetic_examples=phonetic_mappings or None,
        llm_profile=llm_profile,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # All chunks failed (rate limit, network) — segments empty. Don't save
    # over a potentially-good earlier clean. Surface to FE so user knows.
    segs = result.get("segments", [])
    if not segs:
        raise HTTPException(
            status_code=429,
            detail=(
                "Cleaner LLM produced 0 segments — likely rate limit (50/day on "
                "MaaS). Đợi tới ngày mai hoặc switch LLM_MODEL trong .env."
            ),
        )

    # Preserve user edits across regenerate — only refresh `segments` + mapping.
    existing = recording.clean_segments or {}
    existing["segments"] = segs
    existing["cluster_mapping"] = result.get("cluster_mapping", {})
    # Merge in voice-matched names — these are "verified" mappings from DB
    # (Approach D matches). They override LLM-only mapping for those clusters.
    for cid, name in pre_mapped.items():
        existing["cluster_mapping"][cid] = name
    recording.clean_segments = existing
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(recording, "clean_segments")
    await session.flush()

    return {
        "recording_id": recording_id,
        "cached": False,
        "raw_char_count": len(raw_text),
        "clean_segments": result.get("segments", []),
        "cluster_mapping": existing["cluster_mapping"],
        # Which clusters were pre-matched from voiceprint DB (✓ certified)
        "pre_mapped_clusters": list(pre_mapped.keys()),
        # Which clusters HAVE stored embeddings → can be saved as voiceprint
        "available_clusters": available_clusters,
        "edited_html": existing.get("edited_html"),
        "edited_text": existing.get("edited_text"),
    }


@router.patch("/recordings/{recording_id}/clean-edited")
async def save_clean_edited_endpoint(
    recording_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    """Save user-edited Clean transcript (TipTap WYSIWYG).

    Body: { "html": "<...>", "text": "plain text extraction" }
    Stored in recordings.clean_segments as {edited_html, edited_text, segments?}.
    The original LLM-generated `segments` array is preserved alongside so user
    can revert (future feature). MoMGraph prefers edited_text over raw transcript.
    """
    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    html = (payload.get("html") or "").strip()
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty edited text")

    existing = recording.clean_segments or {}

    # ─── Learn from user corrections ────────────────────────────────
    # Diff the original LLM-cleaned segments vs this new edited text to
    # extract single-word fixes the user made. Save them into the global
    # vocab pool — both Whisper (next upload, any project) and the
    # cleaner LLM will see these as phonetic hints. Cross-project.
    # Best-effort: never fails the save itself.
    try:
        # Strip "Speaker:" prefixes from edited text so diff doesn't see
        # those as fake corrections. Cleaner segments are pure body text,
        # but TipTap output renders the speaker label inline.
        import re as _re
        edited_for_diff = _re.sub(
            r"^[^\n]+?:\s+", "", text, flags=_re.MULTILINE,
        )
        original_text = "\n".join(
            (s.get("text") or "").strip()
            for s in (existing.get("segments") or [])
            if (s.get("text") or "").strip()
        )
        if original_text:
            from meeting.vocab_store import (
                extract_corrections_from_edit, bulk_add_corrections,
            )
            pairs = extract_corrections_from_edit(
                original_text, edited_for_diff,
            )
            if pairs:
                added = bulk_add_corrections(pairs)
                logger.info(
                    f"[clean-edited] learned {added} new corrections "
                    f"from {len(pairs)} candidates: "
                    f"{[(p['wrong'], p['correct']) for p in pairs[:5]]}…"
                )
    except Exception as e:
        logger.warning(f"[clean-edited] vocab learn failed (non-fatal): {e}")

    existing["edited_html"] = html
    existing["edited_text"] = text
    recording.clean_segments = existing
    # SQLAlchemy needs a hint for JSONB mutation tracking
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(recording, "clean_segments")
    await session.flush()
    return {"recording_id": recording_id, "edited_chars": len(text)}


@router.post("/recordings/{recording_id}/end")
async def end_recording_endpoint(
    recording_id: str, session: AsyncSession = Depends(get_session)
):
    rid = _parse_uuid(recording_id)
    recording = await repo.end_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {
        "id": str(recording.id),
        "status": recording.status,
        "ended_at": recording.ended_at.isoformat() if recording.ended_at else None,
        "duration_sec": recording.duration_sec,
    }


@router.delete("/recordings/{recording_id}")
async def delete_recording_endpoint(
    recording_id: str, session: AsyncSession = Depends(get_session)
):
    """Hard-delete a recording. FK CASCADE removes its transcript_segments.
    Per-recording MoM (recordings.mom_json) and clean cache (clean_segments)
    are part of the recording row, so they go with it. The parent project's
    project_summary_json is left untouched (may need re-generate)."""
    rid = _parse_uuid(recording_id)
    ok = await repo.delete_recording(session, rid)
    if not ok:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"recording_id": recording_id, "deleted": True}


@router.post("/meetings/{meeting_id}/import-transcript")
async def import_transcript_endpoint(
    meeting_id: str,
    req: TranscriptImport,
    session: AsyncSession = Depends(get_session),
):
    """
    Import raw text transcript → create recording + segments atomically.

    If `replace=True` (default), xoá tất cả recordings cũ của meeting trước khi
    import → tránh accumulate khi user paste nhiều lần.
    Set `replace=False` nếu muốn append (vd multi-session meeting).

    Splits text into segments by newlines, falls back to sentence boundaries.
    """
    mid = _parse_uuid(meeting_id)
    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Empty transcript text")

    deleted_count = 0
    # transcript_changed: when re-importing into an existing recording, compare
    # the new payload to what's already in DB. If unchanged (FE calls
    # /import-transcript on every Gen MoM click as a "sync to DB" step even
    # when nothing edited), skip the wipe-clean + re-trigger-cleaner side
    # effects. Without this guard, every Gen MoM click forces an unnecessary
    # 60-90s LLM re-clean of the same content.
    transcript_changed = True
    if req.recording_id:
        # Target an EXISTING recording → overwrite its segments. No new recording
        # is created. `replace` flag ignored (it's about deleting OTHER recordings).
        from sqlalchemy import delete as sa_delete
        from meeting.db.models import TranscriptSegment
        rid = _parse_uuid(req.recording_id)
        recording = await repo.get_recording(session, rid)
        if not recording or recording.meeting_id != mid:
            raise HTTPException(status_code=404, detail="Recording not found in this meeting")

        # Diff incoming text vs the existing transcript (sum of segment texts).
        # Normalize both sides: strip leading FE source-indicator badges
        # ("[upload] ", "[record] ", "[live] ", "[mic] ") and collapse
        # whitespace. The FE textarea may prefix these to show data source
        # while DB stores raw text — without stripping, the Gen MoM resync
        # would always look "changed" and re-trigger the cleaner.
        import re as _re_norm
        _BADGE_RX = _re_norm.compile(r"^\s*\[(?:upload|record|live|mic)\]\s*")
        existing_text = await repo.join_recording_transcript(session, rid)
        new_text = (req.text or "").strip()
        existing_norm = " ".join(_BADGE_RX.sub("", existing_text).split())
        new_norm = " ".join(_BADGE_RX.sub("", new_text).split())
        if existing_norm and existing_norm == new_norm:
            transcript_changed = False
            logger.info(
                f"[/import-transcript] {rid} payload matches existing "
                f"transcript ({len(new_norm)} chars) — skip wipe + cleaner trigger"
            )

        if transcript_changed:
            # Wipe old segments of this recording
            await session.execute(
                sa_delete(TranscriptSegment).where(TranscriptSegment.recording_id == rid)
            )
            # Invalidate cached clean view since transcript changed
            recording.clean_segments = None
        # Update label if changed (independent of transcript content)
        if req.session_label and req.session_label != recording.session_label:
            recording.session_label = req.session_label
    else:
        if req.replace:
            deleted_count = await repo.delete_all_recordings_for_meeting(session, mid)
        # Create new recording
        recording = await repo.start_recording(
            session, meeting_id=mid, session_label=req.session_label,
        )

    # Prefer structured segments from caller (PhoWhisper response with speaker
    # + timestamps). Falls back to splitting plain text when not provided.
    # Skip rewriting when payload matches existing transcript — segments weren't
    # wiped above so writing again would create duplicates.
    structured_count = 0
    lines: list[str] = []
    if transcript_changed:
        if req.segments:
            for seq, seg in enumerate(req.segments, start=1):
                text = (seg.text or "").strip()
                if not text:
                    continue
                await repo.add_segment(
                    session,
                    recording_id=recording.id,
                    seq=seq,
                    original_text=text,
                    speaker=(seg.speaker or "").strip() or None,
                    start_time_ms=(
                        int(seg.start * 1000) if seg.start is not None else None
                    ),
                    end_time_ms=(
                        int(seg.end * 1000) if seg.end is not None else None
                    ),
                )
                structured_count += 1
            lines = [s.text for s in req.segments if s.text and s.text.strip()]
        else:
            # Plain-text fallback: split by newline then sentence boundary.
            lines = [s.strip() for s in req.text.split("\n") if s.strip()]
            if len(lines) <= 1:
                import re
                lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", req.text) if s.strip()]
            for seq, line in enumerate(lines, start=1):
                await repo.add_segment(
                    session,
                    recording_id=recording.id,
                    seq=seq,
                    original_text=line,
                )

        # Set duration_sec — caller-provided (live record / audio file) takes priority.
        # Otherwise estimate from text: 150 words/min Vietnamese ≈ 2.5 wps.
        if req.duration_sec and req.duration_sec > 0:
            recording.duration_sec = req.duration_sec
        else:
            word_count = sum(len(l.split()) for l in lines)
            # 2.5 words/sec average → seconds = words / 2.5
            recording.duration_sec = max(1, int(word_count / 2.5)) if word_count > 0 else None

    # Persist cluster_embeddings if caller passed them (PhoWhisper-server path)
    if req.cluster_embeddings:
        recording.speaker_embeddings = req.cluster_embeddings
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(recording, "speaker_embeddings")

    # Persist per-cluster 3s WAV samples (local pyannote path). Same write
    # logic as /diarize-result — kept inline rather than extracted because
    # we have only two call sites and the env lookup is short.
    if req.sample_audio_b64:
        import base64
        import os as _os
        output_dir = _os.getenv("OUTPUT_DIR") or _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
            "output",
        )
        rec_dir = _os.path.join(output_dir, str(recording.id))
        _os.makedirs(rec_dir, exist_ok=True)
        paths: dict[str, str] = {}
        for label, b64 in req.sample_audio_b64.items():
            try:
                data = base64.b64decode(b64)
                fpath = _os.path.join(rec_dir, f"spk_{label}.wav")
                with open(fpath, "wb") as f:
                    f.write(data)
                paths[label] = _os.path.relpath(fpath, output_dir)
            except Exception as e:
                import logging as _lg
                _lg.warning(
                    f"[import-transcript] failed to write sample for {label}: {e}"
                )
        if paths:
            recording.speaker_sample_paths = paths
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(recording, "speaker_sample_paths")

    await session.flush()
    # Commit explicitly so the background cleaner sees the new segments
    # (it opens its own fresh session and would otherwise read stale state).
    await session.commit()

    # Kick off background cleaner only when transcript actually changed.
    # The Gen MoM flow re-imports the same text on every click; without this
    # guard, the cleaner would re-run 60-90s per click for identical content
    # and Gen MoM would wait for it. When unchanged, the existing
    # clean_segments cache is still valid → Gen MoM uses it directly.
    if transcript_changed:
        from meeting.services.clean_orchestrator import trigger_background
        trigger_background(str(recording.id))

    # Dispatch async pyannote when caller staged a WAV for diarization
    # (chunked upload path). The task writes speaker_embeddings + sample
    # paths + diarized_text back to this recording when it finishes.
    diarize_task_id: Optional[str] = None
    if req.pending_diarize_path:
        try:
            from meeting.celery_app import is_broker_reachable
            if is_broker_reachable():
                from meeting.tasks import diarize_recording_task
                ar = diarize_recording_task.delay(
                    str(recording.id), req.pending_diarize_path,
                )
                diarize_task_id = ar.id
                import logging as _lg
                _lg.info(
                    f"[import-transcript] dispatched diarize_recording_task "
                    f"id={diarize_task_id} for recording={recording.id}"
                )
            else:
                # Broker down — spawn in-process asyncio task as fallback.
                # Same heavy CPU work, just runs inside the FastAPI worker
                # thread pool instead of a Celery worker. The endpoint
                # itself still returns immediately (we don't await).
                import asyncio as _aio
                import logging as _lg
                _lg.info(
                    "[import-transcript] RabbitMQ unreachable — running "
                    "diarize INLINE via asyncio.create_task (will block "
                    "1 FastAPI worker for the duration)"
                )
                rid_str = str(recording.id)
                pending_path = req.pending_diarize_path

                async def _inline_diarize():
                    # Reuse the Celery task body but invoke it directly.
                    # The task function is a sync callable — wrap in to_thread
                    # so it doesn't block the FastAPI event loop.
                    from meeting.tasks import diarize_recording_task
                    try:
                        await _aio.to_thread(
                            diarize_recording_task.run, rid_str, pending_path,
                        )
                    except Exception as ex:
                        _lg.exception(f"[inline diarize] failed: {ex}")

                _aio.create_task(_inline_diarize())
        except Exception as e:
            import logging as _lg
            _lg.exception(f"[import-transcript] dispatch failed: {e}")

    return {
        "meeting_id": meeting_id,
        "recording_id": str(recording.id),
        "segments_count": len(lines),
        "duration_sec": recording.duration_sec,
        "deleted_recordings": deleted_count,
        # When set, FE polls /api/tasks/{id} for diarize progress and
        # reloads the meeting when state turns SUCCESS.
        "diarize_task_id": diarize_task_id,
    }


@router.post("/recordings/{recording_id}/segments")
async def add_segment_endpoint(
    recording_id: str,
    req: SegmentCreate,
    session: AsyncSession = Depends(get_session),
):
    rid = _parse_uuid(recording_id)
    segment = await repo.add_segment(
        session,
        recording_id=rid,
        seq=req.seq,
        original_text=req.original_text,
        start_time_ms=req.start_time_ms,
        end_time_ms=req.end_time_ms,
        speaker=req.speaker,
    )
    return {
        "id": str(segment.id),
        "seq": segment.seq,
        "original_text": segment.original_text,
    }


@router.post("/meetings/{meeting_id}/clean-transcript")
async def clean_transcript_endpoint(
    meeting_id: str, session: AsyncSession = Depends(get_session)
):
    """
    Sprint C — LLM post-process raw transcript → clean structured view.

    Reads all segments across all recordings of this meeting, calls LLM
    to: detect speakers, group consecutive sentences, remove filler words,
    add punctuation, tag commitment/decision/blocker/etc.

    Returns: {"segments": [{speaker, text, tags}, ...]}
    """
    mid = _parse_uuid(meeting_id)

    meeting = await repo.get_meeting(session, mid)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    raw_text = await repo.join_meeting_transcript(session, mid)
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No transcript segments to clean")

    # Attendees for speaker hint — aggregate across all recordings
    # (project-level field removed in migration 0012). Use union of names.
    names: set[str] = set()
    for r in (meeting.recordings or []):
        for a in (r.attendees or []):
            if isinstance(a, dict) and a.get("name"):
                names.add(a["name"])
    attendees_str = ", ".join(sorted(names))

    import asyncio as _aio_clean2
    result = await _aio_clean2.to_thread(
        clean_transcript,
        raw_text=raw_text,
        attendees=attendees_str,
        vocab_hints=getattr(meeting, "vocab_hints", None),
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {
        "meeting_id": meeting_id,
        "raw_char_count": len(raw_text),
        "clean_segments": result.get("segments", []),
    }


@router.post("/meetings/{meeting_id}/generate-project-summary")
async def generate_project_summary_endpoint(
    meeting_id: str, session: AsyncSession = Depends(get_session)
):
    """Generate / regenerate project summary (tổng kết) from all per-recording MoMs.

    Aggregates decisions across recordings into a chronological timeline + LLM
    narrative. Writes to meetings.project_summary_json. Idempotent — re-run
    after new recordings produce updated summary.
    """
    from meeting.services.project_summarizer import generate_project_summary

    mid = _parse_uuid(meeting_id)
    summary = await generate_project_summary(session, mid)
    if "error" in summary:
        raise HTTPException(status_code=500, detail=summary["error"])
    return {"meeting_id": meeting_id, "summary": summary}


@router.post("/recordings/{recording_id}/generate-mom")
async def generate_recording_mom_endpoint(
    recording_id: str,
    session: AsyncSession = Depends(get_session),
    ui_lang: str = "vi",
):
    """
    Enqueue MoM generation via Celery and return a task_id IMMEDIATELY (202).

    FE polls /api/tasks/{task_id} to check progress + collect result. This
    replaces the old inline `await run_mom_graph()` path so the HTTP request
    doesn't have to wait 2-3 minutes (which was blocking the FE response
    handlers).

    Falls back to inline execution when the broker is unreachable (dev mode
    convenience — `docker compose up -d rabbitmq` not run yet). Inline path
    still uses asyncio.to_thread for the LLM call so it doesn't block the
    event loop.
    """
    _parse_uuid(recording_id)  # validate format only

    from meeting.celery_app import is_broker_reachable
    from meeting.tasks import gen_mom_task

    if is_broker_reachable():
        # Happy path — enqueue task, return task_id. FE polls /tasks/{id}.
        async_result = gen_mom_task.delay(recording_id, ui_lang)
        logger.info(
            f"[/generate-mom] enqueued Celery task {async_result.id} "
            f"for recording {recording_id}"
        )
        return {
            "task_id": async_result.id,
            "recording_id": recording_id,
            "status": "queued",
            "mode": "celery",
        }

    # Fallback — broker down. Dispatch via in-process asyncio so HTTP
    # request returns immediately + FE polls /api/tasks/{id} like with
    # Celery. Keeps UX consistent regardless of broker availability.
    logger.warning(
        "[/generate-mom] Celery broker unreachable — falling back to LOCAL "
        "asyncio task. Start broker with: docker compose up -d rabbitmq"
    )
    output_dir = os.getenv("OUTPUT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output"
    )
    target_rid = recording_id
    target_lang = ui_lang

    async def _run_inline_mom():
        from meeting.db.base import AsyncSessionLocal
        async with AsyncSessionLocal() as s2:
            ckpt = get_checkpointer()
            fs = await run_mom_graph(
                recording_id=target_rid,
                session=s2,
                output_dir=output_dir,
                checkpointer=ckpt,
                mom_language=target_lang,
            )
            if fs.get("error") and not fs.get("mom_json"):
                return {"error": fs["error"]}
            return {
                "recording_id": target_rid,
                "meeting_id": fs.get("meeting_id"),
                "notes": fs.get("mom_json", {}),
                "saved_paths": fs.get("saved_paths", {}),
                "memory_context_count": len(fs.get("memory_context", [])),
            }

    from meeting.services.clean_orchestrator import dispatch_local_task
    local_task_id = dispatch_local_task(_run_inline_mom, prefix="mom-local")
    return {
        "recording_id": recording_id,
        "task_id": local_task_id,
        "status": "queued",
        "mode": "asyncio",
    }


@router.get("/tasks/{task_id}")
async def task_status_endpoint(task_id: str):
    """Poll task state. FE polls this for both Celery-dispatched tasks
    AND in-process asyncio fallback tasks — uniform interface.

    Returns {state, result?, error?} — state in PENDING / STARTED /
    SUCCESS / FAILURE / RETRY. Local task_ids carry a `local-` prefix.
    """
    # Local (in-process asyncio) tasks first — cheap dict lookup, and
    # local task_ids have a distinct prefix so we can route quickly.
    if task_id.startswith(("local-", "clean-local-", "mom-local-")):
        from meeting.services.clean_orchestrator import get_local_task_state
        state = get_local_task_state(task_id)
        if state is not None:
            return state
    from meeting.tasks import get_task_state
    return get_task_state(task_id)


@router.get("/recordings/{recording_id}/mom")
async def get_recording_mom_endpoint(
    recording_id: str, session: AsyncSession = Depends(get_session)
):
    """Return cached MoM for a recording (404 if not generated yet)."""
    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    if not recording.mom_json:
        raise HTTPException(status_code=404, detail="MoM chưa được tạo")
    return {"recording_id": recording_id, "mom_json": recording.mom_json}


@router.get("/recordings/{recording_id}/download")
async def download_recording_mom(
    recording_id: str,
    fmt: str = "md",
    session: AsyncSession = Depends(get_session),
):
    """Download per-recording MoM as Markdown (fmt=md) or JSON (fmt=json)."""
    from fastapi.responses import FileResponse, JSONResponse
    from meeting.report_generator import generate_mom_markdown
    import os as _os

    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    if not recording.mom_json:
        raise HTTPException(status_code=400, detail="MoM chưa được tạo cho phiên này")

    if fmt == "json":
        return JSONResponse(content=recording.mom_json)

    out_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "output")
    md_path = generate_mom_markdown(
        notes=recording.mom_json,
        output_dir=out_dir,
        recording_label=recording.session_label,
    )
    return FileResponse(
        md_path,
        media_type="text/markdown",
        filename=_os.path.basename(md_path),
    )


# ═══════════════════════════════════════════════════════════════════════
# Voiceprints — zero-shot speaker ID
# ═══════════════════════════════════════════════════════════════════════

class VoiceprintBind(BaseModel):
    """Bind a recording's cluster_id → a name. Backend pulls the embedding
    from recording.speaker_embeddings and saves to speaker_voiceprints."""
    cluster_id: str   # "SPEAKER_00"
    name: str


class VoiceprintRename(BaseModel):
    name: str


@router.post("/recordings/{recording_id}/voiceprints")
async def bind_voiceprint_endpoint(
    recording_id: str,
    req: VoiceprintBind,
    session: AsyncSession = Depends(get_session),
):
    """User labelled a SPEAKER_NN with a real name → save its embedding to
    the user's voiceprint DB so future meetings auto-recognise this voice.

    Also propagates the new name into recording.clean_segments so the Clean
    view text + cluster_mapping stay in sync (text "Đại:" → "Đại Nghi:").
    """
    from datetime import datetime
    from meeting.db.repositories_voiceprint import (
        save_voiceprint,
        find_similar_voiceprint,
    )
    from sqlalchemy import delete as _sa_delete
    from sqlalchemy.orm.attributes import flag_modified

    rid = _parse_uuid(recording_id)
    recording = await repo.get_recording(session, rid)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")
    embeddings = recording.speaker_embeddings or {}
    emb = embeddings.get(req.cluster_id)
    new_name = req.name.strip()
    user = await repo.get_or_create_dev_user(session)
    vp = None
    voiceprint_saved = False

    # Legacy recordings (uploaded before Phase 2) have no embeddings stored,
    # so we can't save a voiceprint — but the user still wants the text
    # rename to persist (rename "SPEAKER_00:" → "Thầy Thông:" in the Clean
    # view). Skip the voiceprint write and fall through to the text-rename
    # block below.
    from meeting.db.models import SpeakerVoiceprint as _SV
    close: list = []
    if emb:
        # Voice-level dedup: if this embedding is already very close to an
        # existing voiceprint (regardless of stored name), treat this bind as a
        # RENAME instead of inserting a duplicate row. This prevents the
        # "same voice stored under multiple names" bug when the user changes
        # their mind about a speaker's label and re-saves.
        #
        # Threshold 0.15 ≈ 0.85 cosine similarity — comfortably above the
        # default 0.30 match threshold; only fires when we're highly confident
        # it's literally the same voice.
        close = await find_similar_voiceprint(
            session, user_id=user.id, embedding=emb, threshold=0.15, limit=5,
        )
    if emb and close:
        # Keep the closest match; rename it to new_name and merge the new
        # sample into its running mean. Delete any OTHER very-close rows
        # (they are duplicates of the same voice, created by the same bug).
        primary_vp, _ = close[0]

        # Edge case: user types a name that ALREADY belongs to a DIFFERENT
        # voiceprint row (e.g. saved last week from another meeting). Renaming
        # primary_vp → new_name would violate the (user_id, name) unique
        # constraint. Resolve by treating this as "this voice and the existing
        # 'new_name' row are the same person" → merge our new sample into the
        # existing-by-name row + DELETE primary_vp (which is a stale near-dup).
        from sqlalchemy import select as _sql_select
        existing_by_name = (
            await session.execute(
                _sql_select(_SV).where(
                    _SV.user_id == user.id,
                    _SV.name == new_name,
                    _SV.id != primary_vp.id,
                )
            )
        ).scalar_one_or_none()
        if existing_by_name is not None:
            # Merge new sample into the already-existing "new_name" row.
            n = existing_by_name.sample_count
            existing_by_name.embedding = [
                (n * old + new) / (n + 1)
                for old, new in zip(existing_by_name.embedding, emb)
            ]
            existing_by_name.sample_count = n + 1
            existing_by_name.last_seen_at = datetime.utcnow()
            # Drop primary_vp + any other near-dup rows since they all map to
            # this same person now.
            stale_ids = [primary_vp.id] + [_vp.id for _vp, _ in close[1:]]
            stale_ids = [sid for sid in stale_ids if sid != existing_by_name.id]
            if stale_ids:
                await session.execute(
                    _sa_delete(_SV).where(_SV.id.in_(stale_ids))
                )
            await session.flush()
            vp = existing_by_name
            voiceprint_saved = True
        else:
            # Safe to rename primary_vp — no other row claims new_name.
            n = primary_vp.sample_count
            primary_vp.embedding = [
                (n * old + new) / (n + 1)
                for old, new in zip(primary_vp.embedding, emb)
            ]
            primary_vp.sample_count = n + 1
            primary_vp.last_seen_at = datetime.utcnow()
            primary_vp.name = new_name
            # Drop any other near-duplicate rows for the same voice.
            dup_ids = [_vp.id for _vp, _ in close[1:]]
            if dup_ids:
                await session.execute(
                    _sa_delete(_SV).where(_SV.id.in_(dup_ids))
                )
            await session.flush()
            vp = primary_vp
            voiceprint_saved = True
    elif emb:
        # No cosine-close match → INSERT new row. But the (user_id, name)
        # unique index still applies: if user types a name that already
        # exists (with a DIFFERENT voice ≥ 0.85 cosine away), the INSERT
        # would crash. Merge into existing-by-name row instead — treats it
        # as adding another sample to the same person's print.
        from sqlalchemy import select as _sql_select
        existing_by_name = (
            await session.execute(
                _sql_select(_SV).where(
                    _SV.user_id == user.id,
                    _SV.name == new_name,
                )
            )
        ).scalar_one_or_none()
        if existing_by_name is not None:
            n = existing_by_name.sample_count
            existing_by_name.embedding = [
                (n * old + new) / (n + 1)
                for old, new in zip(existing_by_name.embedding, emb)
            ]
            existing_by_name.sample_count = n + 1
            existing_by_name.last_seen_at = datetime.utcnow()
            await session.flush()
            vp = existing_by_name
        else:
            vp = await save_voiceprint(
                session,
                user_id=user.id,
                name=new_name,
                embedding=emb,
            )
        voiceprint_saved = True
    # else: emb missing → skip voiceprint, still rename text below

    # Propagate rename into clean_segments so the Clean view text + mapping
    # immediately reflect the user's chosen name (avoid "Đại:" lingering after
    # save when user typed "Đại Nghi").
    #
    # Names worth renaming for THIS cluster:
    #   - old_name from cluster_mapping (LLM-inferred name, e.g. "Đại")
    #   - the raw cluster_id itself (when LLM couldn't infer a name, segments
    #     store "SPEAKER_NN" verbatim — see cleaner prompt)
    # We DO NOT rename bare "Unknown" because Unknown could belong to multiple
    # clusters, so renaming it would incorrectly relabel another speaker's lines.
    clean = recording.clean_segments or {}
    mapping = dict(clean.get("cluster_mapping", {}))
    old_name = mapping.get(req.cluster_id)
    mapping[req.cluster_id] = new_name
    clean["cluster_mapping"] = mapping
    rename_from: list[str] = []
    if old_name and old_name not in ("Unknown", new_name):
        rename_from.append(old_name)
    if req.cluster_id != new_name:
        rename_from.append(req.cluster_id)  # raw "SPEAKER_NN" fallback
    if rename_from:
        from html import escape as _esc
        for seg in clean.get("segments", []):
            if seg.get("speaker") in rename_from:
                seg["speaker"] = new_name
        eh = clean.get("edited_html")
        if eh and isinstance(eh, str):
            for src in rename_from:
                eh = eh.replace(
                    f"<strong>{_esc(src)}:</strong>",
                    f"<strong>{_esc(new_name)}:</strong>",
                )
            clean["edited_html"] = eh
    recording.clean_segments = clean
    flag_modified(recording, "clean_segments")
    await session.flush()

    return {
        "id": str(vp.id) if vp else None,
        "name": new_name,
        "sample_count": vp.sample_count if vp else 0,
        "cluster_id": req.cluster_id,
        "old_name": old_name,
        "new_name": new_name,
        "voiceprint_saved": voiceprint_saved,
    }


@router.get("/voiceprints")
async def list_voiceprints_endpoint(
    session: AsyncSession = Depends(get_session),
):
    from meeting.db.repositories_voiceprint import list_voiceprints

    user = await repo.get_or_create_dev_user(session)
    rows = await list_voiceprints(session, user.id)
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "sample_count": r.sample_count,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.patch("/voiceprints/{voiceprint_id}")
async def rename_voiceprint_endpoint(
    voiceprint_id: str,
    req: VoiceprintRename,
    session: AsyncSession = Depends(get_session),
):
    from meeting.db.repositories_voiceprint import rename_voiceprint

    vp_id = _parse_uuid(voiceprint_id)
    user = await repo.get_or_create_dev_user(session)
    vp = await rename_voiceprint(session, vp_id, user.id, req.name)
    if not vp:
        raise HTTPException(status_code=404, detail="Voiceprint not found")
    return {"id": str(vp.id), "name": vp.name}


@router.delete("/voiceprints/{voiceprint_id}")
async def delete_voiceprint_endpoint(
    voiceprint_id: str,
    session: AsyncSession = Depends(get_session),
):
    from meeting.db.repositories_voiceprint import delete_voiceprint

    vp_id = _parse_uuid(voiceprint_id)
    user = await repo.get_or_create_dev_user(session)
    ok = await delete_voiceprint(session, vp_id, user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Voiceprint not found")
    return {"deleted": True}
