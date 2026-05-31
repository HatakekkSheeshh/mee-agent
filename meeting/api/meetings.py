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
    title: str = "Untitled meeting"
    purpose: str = ""
    venue: str = ""
    date: Optional[date_type] = None
    chaired_by: str = ""
    noted_by: str = ""
    attendees: Optional[list] = None  # [{name, department, title}, ...]


class MeetingOut(BaseModel):
    id: str
    title: str
    purpose: Optional[str]
    venue: Optional[str]
    date: Optional[date_type]
    chaired_by: Optional[str]
    noted_by: Optional[str]
    attendees: Optional[list]
    status: str
    # Per-recording MoM existence is fetched separately. has_summary = whether
    # the project-level summary has been generated at least once.
    has_summary: bool = False
    is_pinned: bool = False


class MeetingPatch(BaseModel):
    title: Optional[str] = None
    is_pinned: Optional[bool] = None
    purpose: Optional[str] = None
    venue: Optional[str] = None
    date: Optional[date_type] = None
    chaired_by: Optional[str] = None
    noted_by: Optional[str] = None
    attendees: Optional[list] = None


class SegmentCreate(BaseModel):
    seq: int
    original_text: str
    start_time_ms: Optional[int] = None
    end_time_ms: Optional[int] = None
    speaker: Optional[str] = None


class RecordingCreate(BaseModel):
    session_label: Optional[str] = None


class TranscriptImport(BaseModel):
    text: str
    # Default None — only override the recording's session_label if caller
    # explicitly passes a value. Previously defaulted to "Imported transcript"
    # which silently renamed recordings created with a meaningful label.
    session_label: Optional[str] = None
    replace: bool = True  # True = xoá recordings cũ trước khi import (chỉ áp khi recording_id=None)
    duration_sec: Optional[int] = None  # caller can pass actual duration (live record, audio file)
    recording_id: Optional[str] = None  # target an EXISTING recording — overwrites its segments


# ─── Helpers ──────────────────────────────────────────────────────

def _meeting_to_out(m) -> MeetingOut:
    return MeetingOut(
        id=str(m.id),
        title=m.title,
        purpose=m.purpose,
        venue=m.venue,
        date=m.date,
        chaired_by=m.chaired_by,
        noted_by=m.noted_by,
        attendees=m.attendees,
        status=m.status,
        has_summary=getattr(m, "project_summary_json", None) is not None,
        is_pinned=getattr(m, "is_pinned", False),
    )


def _parse_uuid(s: str) -> uuid.UUID:
    try:
        return uuid.UUID(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {s}")


# ─── Endpoints ────────────────────────────────────────────────────

@router.post("/meetings", response_model=MeetingOut)
async def create_meeting_endpoint(
    req: MeetingCreate, session: AsyncSession = Depends(get_session)
):
    user = await repo.get_or_create_dev_user(session)
    meeting = await repo.create_meeting(
        session,
        user_id=user.id,
        title=req.title,
        purpose=req.purpose,
        venue=req.venue,
        meeting_date=req.date,
        chaired_by=req.chaired_by,
        noted_by=req.noted_by,
        attendees=req.attendees,
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
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "duration_sec": r.duration_sec,
                "status": r.status,
                "segment_count": len([s for s in r.segments if not s.is_deleted]),
                "mom_json": r.mom_json,
                "has_clean": r.clean_segments is not None,
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
    """Update title and/or pin state."""
    mid = _parse_uuid(meeting_id)
    updated = await repo.update_meeting(
        session, mid, title=req.title, is_pinned=req.is_pinned
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
    # Count non-deleted segments
    seg_count = await session.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(
            TranscriptSegment.recording_id == rid,
            TranscriptSegment.is_deleted.is_(False),
        )
    )
    return {
        "recording_id": recording_id,
        "meeting_id": str(recording.meeting_id),
        "session_label": recording.session_label,
        "transcript": text,
        "segment_count": seg_count or 0,
        "duration_sec": recording.duration_sec,
        "started_at": recording.started_at.isoformat() if recording.started_at else None,
        "ended_at": recording.ended_at.isoformat() if recording.ended_at else None,
    }


class RecordingPatch(BaseModel):
    session_label: Optional[str] = None


@router.patch("/recordings/{recording_id}")
async def patch_recording_endpoint(
    recording_id: str,
    req: RecordingPatch,
    session: AsyncSession = Depends(get_session),
):
    """Rename a recording (update session_label)."""
    rid = _parse_uuid(recording_id)
    updated = await repo.update_recording_label(session, rid, req.session_label or "")
    if not updated:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {
        "recording_id": str(updated.id),
        "session_label": updated.session_label,
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

    # Return cache hit unless caller forces regenerate
    if not regenerate and recording.clean_segments:
        cached = recording.clean_segments or {}
        return {
            "recording_id": recording_id,
            "cached": True,
            "clean_segments": cached.get("segments", []),
            "edited_html": cached.get("edited_html"),
            "edited_text": cached.get("edited_text"),
        }

    raw_text = await repo.join_recording_transcript(session, rid)
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No segments to clean")

    meeting = await repo.get_meeting(session, recording.meeting_id)
    attendees_str = ""
    if meeting and meeting.attendees:
        attendees_str = ", ".join(
            a.get("name", "") for a in meeting.attendees if isinstance(a, dict)
        )

    result = clean_transcript(raw_text=raw_text, attendees=attendees_str)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # Preserve user edits across regenerate — only refresh `segments`.
    existing = recording.clean_segments or {}
    existing["segments"] = result.get("segments", [])
    recording.clean_segments = existing
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(recording, "clean_segments")
    await session.flush()

    return {
        "recording_id": recording_id,
        "cached": False,
        "raw_char_count": len(raw_text),
        "clean_segments": result.get("segments", []),
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
    if req.recording_id:
        # Target an EXISTING recording → overwrite its segments. No new recording
        # is created. `replace` flag ignored (it's about deleting OTHER recordings).
        from sqlalchemy import delete as sa_delete
        from meeting.db.models import TranscriptSegment
        rid = _parse_uuid(req.recording_id)
        recording = await repo.get_recording(session, rid)
        if not recording or recording.meeting_id != mid:
            raise HTTPException(status_code=404, detail="Recording not found in this meeting")
        # Wipe old segments of this recording
        await session.execute(
            sa_delete(TranscriptSegment).where(TranscriptSegment.recording_id == rid)
        )
        # Update label if changed
        if req.session_label and req.session_label != recording.session_label:
            recording.session_label = req.session_label
        # Invalidate cached clean view since transcript changed
        recording.clean_segments = None
    else:
        if req.replace:
            deleted_count = await repo.delete_all_recordings_for_meeting(session, mid)
        # Create new recording
        recording = await repo.start_recording(
            session, meeting_id=mid, session_label=req.session_label,
        )

    # Split text into segments
    lines = [s.strip() for s in req.text.split("\n") if s.strip()]
    if len(lines) <= 1:
        # No newlines → split by sentence boundary
        import re
        lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", req.text) if s.strip()]

    # Bulk insert segments
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
    await session.flush()

    return {
        "meeting_id": meeting_id,
        "recording_id": str(recording.id),
        "segments_count": len(lines),
        "duration_sec": recording.duration_sec,
        "deleted_recordings": deleted_count,
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

    # Attendees for speaker hint
    attendees_str = ""
    if meeting.attendees:
        attendees_str = ", ".join(
            a.get("name", "") for a in meeting.attendees if isinstance(a, dict)
        )

    result = clean_transcript(raw_text=raw_text, attendees=attendees_str)

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
    recording_id: str, session: AsyncSession = Depends(get_session)
):
    """
    Generate per-recording MoM (biên bản phiên họp) via LangGraph:
        load_transcript → read_memory → generate_mom → save_results

    Output saved to recordings.mom_json. thread_id = recording_id so each
    recording has its own resume state on the PostgresSaver checkpointer.
    """
    _parse_uuid(recording_id)  # validate format only

    output_dir = os.getenv("OUTPUT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output"
    )
    checkpointer = get_checkpointer()

    final_state = await run_mom_graph(
        recording_id=recording_id,
        session=session,
        output_dir=output_dir,
        checkpointer=checkpointer,
    )

    if final_state.get("error") and not final_state.get("mom_json"):
        raise HTTPException(status_code=500, detail=final_state["error"])

    return {
        "recording_id": recording_id,
        "meeting_id": final_state.get("meeting_id"),
        "notes": final_state.get("mom_json", {}),
        "saved_paths": final_state.get("saved_paths", {}),
        "memory_context_count": len(final_state.get("memory_context", [])),
    }


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
