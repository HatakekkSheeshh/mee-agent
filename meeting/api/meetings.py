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
    has_mom: bool


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
    session_label: Optional[str] = "Imported transcript"
    replace: bool = True  # True = xoá recordings cũ trước khi import


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
        has_mom=m.mom_json is not None,
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
            }
            for r in meeting.recordings
        ],
        "mom_json": meeting.mom_json,
    }


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

    return {
        "meeting_id": meeting_id,
        "recording_id": str(recording.id),
        "segments_count": len(lines),
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


@router.post("/meetings/{meeting_id}/generate-mom")
async def generate_mom_endpoint(
    meeting_id: str, session: AsyncSession = Depends(get_session)
):
    """
    Generate MoM via LangGraph (Phase B):
        load_transcript → read_memory → generate_mom → save_results

    With PostgresSaver checkpointing — fail at any node → re-invoke
    resumes from that node (uses thread_id = meeting_id).
    """
    _parse_uuid(meeting_id)  # validate format only

    output_dir = os.getenv("OUTPUT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output"
    )
    checkpointer = get_checkpointer()

    final_state = await run_mom_graph(
        meeting_id=meeting_id,
        session=session,
        output_dir=output_dir,
        checkpointer=checkpointer,
    )

    if final_state.get("error"):
        raise HTTPException(status_code=500, detail=final_state["error"])

    return {
        "meeting_id": meeting_id,
        "notes": final_state.get("mom_json", {}),
        "saved_paths": final_state.get("saved_paths", {}),
        "memory_context_count": len(final_state.get("memory_context", [])),
    }
