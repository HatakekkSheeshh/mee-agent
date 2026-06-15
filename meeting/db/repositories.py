"""
Repository layer — DB access patterns for Mee.

Pattern: each function takes an AsyncSession + parameters, returns ORM objects.
No business logic here — just data access.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from meeting.db.models import (
    AuditLog,
    ChatMessage,
    ChatSession,
    Meeting,
    MeetingMember,
    MemoryEventRow,
    PendingAction,
    Recording,
    Role,
    SpeakerVoiceprint,
    TranscriptSegment,
    User,
)
from meeting.services.role_mapping import resolve_role


# ─── User ─────────────────────────────────────────────────────────

async def get_or_create_dev_user(session: AsyncSession) -> User:
    """Pre-auth bootstrap — returns a fixed dev user until M365 OAuth is wired."""
    DEV_MS_OID = "dev-local-user"
    stmt = select(User).where(User.ms_oid == DEV_MS_OID)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user:
        return user
    user = User(
        ms_oid=DEV_MS_OID,
        email="user@vng.com.vn",
        display_name="User",
    )
    session.add(user)
    await session.flush()
    return user


# ─── Roles (persona pool) ─────────────────────────────────────────

async def get_role(session: AsyncSession, name: str) -> Optional[Role]:
    """Fetch one role from the pool by its unique name, or None on miss."""
    stmt = select(Role).where(Role.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_roles(session: AsyncSession) -> Sequence[Role]:
    """All roles in the pool, oldest first (stable enumeration order)."""
    stmt = select(Role).order_by(Role.created_at)
    return (await session.execute(stmt)).scalars().all()


async def resolve_role_by_title(session: AsyncSession, title: str | None) -> Optional[str]:
    """Resolve a free-text jobTitle to a canonical roles.name, or None.

    Loads the pool and delegates to the pure `resolve_role`. None on no match
    (the caller leaves role_id NULL → generic kickoff).
    """
    roles = await list_roles(session)
    return resolve_role(title, roles)


async def add_role_alias(session: AsyncSession, role_id: uuid.UUID, alias: str) -> None:
    """Append `alias` to a role's aliases array, skipping if already present.

    Dedup via `NOT (:alias = ANY(aliases))` (exact match). Stores the alias
    verbatim — the raw jobTitle is intended, since `resolve_role` normalizes at
    lookup time. The caller owns the transaction (no commit here).
    Single-instance cron → no locking.
    """
    stmt = text(
        "UPDATE roles SET aliases = array_append(aliases, :alias) "
        "WHERE id = :role_id AND NOT (:alias = ANY(aliases))"
    )
    await session.execute(stmt, {"alias": alias, "role_id": role_id})


# ─── Meeting ──────────────────────────────────────────────────────

async def create_meeting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    vocab_hints: Optional[str] = None,
) -> Meeting:
    """Create a project. Per-meeting-event metadata is added later on the
    first recording (via update_recording_metadata)."""
    meeting = Meeting(
        user_id=user_id,
        title=title or "Untitled meeting",
        vocab_hints=(vocab_hints or "").strip() or None,
    )
    session.add(meeting)
    await session.flush()

    # Auto-create owner membership for the creator
    session.add(MeetingMember(
        meeting_id=meeting.id,
        user_id=user_id,
        role="owner",
        invited_by=user_id,
        accepted_at=datetime.utcnow(),
    ))
    await session.flush()
    return meeting


async def get_meeting(session: AsyncSession, meeting_id: uuid.UUID) -> Optional[Meeting]:
    stmt = (
        select(Meeting)
        .where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
        .options(selectinload(Meeting.recordings).selectinload(Recording.segments))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_meetings_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[Meeting]:
    """List meetings where user is an active member (any role).

    Sort: pinned first, then by creation date (newest first).
    """
    stmt = (
        select(Meeting)
        .join(MeetingMember, MeetingMember.meeting_id == Meeting.id)
        .where(
            MeetingMember.user_id == user_id,
            MeetingMember.revoked_at.is_(None),
            Meeting.deleted_at.is_(None),
        )
        .order_by(Meeting.is_pinned.desc(), Meeting.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


async def find_meetings_by_title(
    session: AsyncSession, user_id: uuid.UUID, q: str
) -> Sequence[Meeting]:
    """ILIKE-search the user's meetings by title fragment, most-recent first.

    User-scoped via MeetingMember (mirrors list_meetings_for_user). Used by the
    chat agent to resolve a project the user names by title. Returns [] for a
    blank query.
    """
    q = (q or "").strip()
    if not q:
        return []
    stmt = (
        select(Meeting)
        .join(MeetingMember, MeetingMember.meeting_id == Meeting.id)
        .where(
            MeetingMember.user_id == user_id,
            MeetingMember.revoked_at.is_(None),
            Meeting.deleted_at.is_(None),
            Meeting.title.ilike(f"%{q}%"),
        )
        .order_by(Meeting.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


async def update_meeting(
    session: AsyncSession,
    meeting_id: uuid.UUID,
    *,
    title: Optional[str] = None,
    is_pinned: Optional[bool] = None,
    vocab_hints: Optional[str] = None,
    stt_model: Optional[str] = None,
    llm_model: Optional[str] = None,
    mom_language: Optional[str] = None,
) -> Optional[Meeting]:
    """Patch project-level fields. Per-meeting-event fields (date, attendees,
    chair...) live on recordings now — use update_recording_metadata for those.
    """
    meeting = await session.get(Meeting, meeting_id)
    if not meeting or meeting.deleted_at is not None:
        return None
    if title is not None and title.strip():
        meeting.title = title.strip()
    if is_pinned is not None:
        meeting.is_pinned = is_pinned
    if vocab_hints is not None:
        meeting.vocab_hints = vocab_hints.strip() or None
    if stt_model is not None:
        meeting.stt_model = stt_model.strip() or None
    if llm_model is not None:
        meeting.llm_model = llm_model.strip() or None
    if mom_language is not None:
        meeting.mom_language = mom_language.strip() or None
    await session.flush()
    return meeting


async def update_recording_metadata(
    session: AsyncSession,
    recording_id: uuid.UUID,
    *,
    title: Optional[str] = None,
    purpose: Optional[str] = None,
    date=None,                          # datetime.date | None
    venue: Optional[str] = None,
    chaired_by: Optional[str] = None,
    noted_by: Optional[str] = None,
    attendees: Optional[list] = None,
    vocab_hints: Optional[str] = None,
    session_label: Optional[str] = None,
    stt_model: Optional[str] = None,
    llm_model: Optional[str] = None,
    mom_language: Optional[str] = None,
) -> Optional["Recording"]:  # type: ignore[name-defined]
    """Patch per-recording metadata. All fields optional."""
    from meeting.db.models import Recording
    rec = await session.get(Recording, recording_id)
    if not rec:
        return None
    if title is not None:
        rec.title = title.strip() or None
    if purpose is not None:
        rec.purpose = purpose.strip() or None
    if date is not None:
        rec.date = date
    if venue is not None:
        rec.venue = venue.strip() or None
    if chaired_by is not None:
        rec.chaired_by = chaired_by.strip() or None
    if noted_by is not None:
        rec.noted_by = noted_by.strip() or None
    if attendees is not None:
        rec.attendees = attendees
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(rec, "attendees")
    if vocab_hints is not None:
        rec.vocab_hints = vocab_hints.strip() or None
    if session_label is not None and session_label.strip():
        rec.session_label = session_label.strip()
    # Model fields — accept the sentinel "" to clear back to inherit-from-meeting,
    # otherwise must be one of the registered IDs (caller validates).
    if stt_model is not None:
        rec.stt_model = stt_model.strip() or None
    if llm_model is not None:
        rec.llm_model = llm_model.strip() or None
    if mom_language is not None:
        rec.mom_language = mom_language.strip() or None
    await session.flush()
    return rec


async def save_recording_phonetic(
    session: AsyncSession,
    recording_id: uuid.UUID,
    phonetic_json: dict,
) -> None:
    """Persist LLM-generated phonetic mappings for the cleaner few-shot slot.
    Called by the phonetic_generator after a successful regeneration."""
    rec = await session.get(Recording, recording_id)
    if not rec:
        return
    rec.phonetic_examples_json = phonetic_json
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(rec, "phonetic_examples_json")
    await session.flush()


async def soft_delete_meeting(
    session: AsyncSession, meeting_id: uuid.UUID
) -> bool:
    """Mark meeting as deleted (does not cascade to recordings/segments)."""
    from datetime import datetime, timezone
    meeting = await session.get(Meeting, meeting_id)
    if not meeting or meeting.deleted_at is not None:
        return False
    meeting.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    return True


async def save_recording_mom(
    session: AsyncSession, recording_id: uuid.UUID, mom_json: dict
) -> None:
    """Save per-recording MoM (biên bản phiên họp) to recordings.mom_json."""
    recording = await session.get(Recording, recording_id)
    if recording:
        recording.mom_json = mom_json
        await session.flush()


async def save_project_summary(
    session: AsyncSession, meeting_id: uuid.UUID, summary_json: dict
) -> None:
    """Save project-level summary (tổng kết) to meetings.project_summary_json."""
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.project_summary_json = summary_json
        await session.flush()


# ─── Recording ────────────────────────────────────────────────────

async def start_recording(
    session: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    session_label: Optional[str] = None,
) -> Recording:
    """Create a new recording. Auto-inherits venue/chaired_by/noted_by/
    attendees from the latest recording in the same project so user doesn't
    have to retype context each session (per-meeting-event fields like
    title/purpose/date stay empty — user fills per session)."""
    from sqlalchemy import select as _select, desc as _desc
    prev_stmt = (
        _select(Recording)
        .where(Recording.meeting_id == meeting_id)
        .order_by(_desc(Recording.started_at))
        .limit(1)
    )
    prev = (await session.execute(prev_stmt)).scalars().first()
    recording = Recording(
        meeting_id=meeting_id,
        session_label=session_label,
        status="recording",
        # Inherit "context" fields from previous recording (if any).
        # These are usually stable across sessions of the same project.
        venue=prev.venue if prev else None,
        chaired_by=prev.chaired_by if prev else None,
        noted_by=prev.noted_by if prev else None,
        attendees=prev.attendees if prev else None,
        # Auto-inherit model picks too — user only retypes when switching.
        stt_model=prev.stt_model if prev else None,
        llm_model=prev.llm_model if prev else None,
    )
    session.add(recording)
    await session.flush()
    return recording


async def get_recording(
    session: AsyncSession, recording_id: uuid.UUID
) -> Optional[Recording]:
    return await session.get(Recording, recording_id)


async def update_recording_label(
    session: AsyncSession, recording_id: uuid.UUID, label: str
) -> Optional[Recording]:
    """Rename a recording's session_label."""
    recording = await session.get(Recording, recording_id)
    if not recording:
        return None
    if label and label.strip():
        recording.session_label = label.strip()
        await session.flush()
    return recording


async def join_recording_transcript(
    session: AsyncSession, recording_id: uuid.UUID
) -> str:
    """Return joined text of all segments in 1 recording (COALESCE edited/original).

    Prefixes lines with `SPEAKER_NN:` when the segment has a stored speaker —
    gives the cleaner LLM ground-truth anchors instead of having to infer
    speakers purely from context.
    """
    stmt = (
        select(TranscriptSegment)
        .where(
            TranscriptSegment.recording_id == recording_id,
            TranscriptSegment.is_deleted.is_(False),
        )
        .order_by(TranscriptSegment.seq)
    )
    segments = (await session.execute(stmt)).scalars().all()
    out: list[str] = []
    last_speaker: Optional[str] = None
    for s in segments:
        spk = (s.speaker or "").strip() or None
        # Format timestamp prefix [mm:ss] when start_time_ms available
        ts_prefix = ""
        if s.start_time_ms is not None:
            sec = s.start_time_ms // 1000
            ts_prefix = f"[{sec // 60:02d}:{sec % 60:02d}] "
        if spk and spk != last_speaker:
            out.append(f"{ts_prefix}{spk}: {s.text}")
            last_speaker = spk
        else:
            out.append(f"{ts_prefix}{s.text}" if ts_prefix else s.text)
            if not spk:
                last_speaker = None
    return "\n".join(out)


async def end_recording(
    session: AsyncSession, recording_id: uuid.UUID
) -> Optional[Recording]:
    recording = await session.get(Recording, recording_id)
    if not recording:
        return None
    now = datetime.utcnow()
    recording.ended_at = now
    if recording.started_at:
        delta = now - recording.started_at.replace(tzinfo=None)
        recording.duration_sec = int(delta.total_seconds())
    recording.status = "done"
    await session.flush()
    return recording


async def delete_all_recordings_for_meeting(
    session: AsyncSession, meeting_id: uuid.UUID
) -> int:
    """Hard-delete all recordings (segments cascaded via FK). Returns count deleted."""
    # Count first for return value
    count_stmt = select(Recording).where(Recording.meeting_id == meeting_id)
    rows = (await session.execute(count_stmt)).scalars().all()
    count = len(rows)
    # Delete (FK CASCADE removes segments)
    await session.execute(
        delete(Recording).where(Recording.meeting_id == meeting_id)
    )
    await session.flush()
    return count


async def delete_recording(
    session: AsyncSession, recording_id: uuid.UUID
) -> bool:
    """Hard-delete a single recording. FK CASCADE removes transcript_segments.
    Returns True if a row was deleted, False if not found."""
    recording = await session.get(Recording, recording_id)
    if not recording:
        return False
    await session.delete(recording)
    await session.flush()
    return True


# ─── Segments ─────────────────────────────────────────────────────

async def add_segment(
    session: AsyncSession,
    *,
    recording_id: uuid.UUID,
    seq: int,
    original_text: str,
    start_time_ms: Optional[int] = None,
    end_time_ms: Optional[int] = None,
    speaker: Optional[str] = None,
    words: Optional[list] = None,
) -> TranscriptSegment:
    segment = TranscriptSegment(
        recording_id=recording_id,
        seq=seq,
        original_text=original_text,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        speaker=speaker,
        words=words,
    )
    session.add(segment)
    await session.flush()
    return segment


async def list_segments(
    session: AsyncSession, recording_id: uuid.UUID
) -> Sequence[TranscriptSegment]:
    stmt = (
        select(TranscriptSegment)
        .where(
            TranscriptSegment.recording_id == recording_id,
            TranscriptSegment.is_deleted.is_(False),
        )
        .order_by(TranscriptSegment.seq)
    )
    return (await session.execute(stmt)).scalars().all()


async def edit_segment(
    session: AsyncSession,
    segment_id: uuid.UUID,
    *,
    edited_text: str,
    edited_by: uuid.UUID,
) -> Optional[TranscriptSegment]:
    segment = await session.get(TranscriptSegment, segment_id)
    if not segment:
        return None
    segment.edited_text = edited_text
    segment.edited_by = edited_by
    segment.edited_at = datetime.utcnow()
    await session.flush()
    return segment


async def soft_delete_segment(
    session: AsyncSession, segment_id: uuid.UUID
) -> bool:
    segment = await session.get(TranscriptSegment, segment_id)
    if not segment:
        return False
    segment.is_deleted = True
    await session.flush()
    return True


async def get_mom_action_items(
    session: AsyncSession, meeting_id: uuid.UUID
) -> list[dict]:
    """Aggregate action_items from every recording's MoM for a meeting.

    Each item follows the note_generator shape: {pic, deadline, item}.
    Returns [] if the meeting/recordings have no MoM yet.
    """
    meeting = await get_meeting(session, meeting_id)
    if not meeting:
        return []
    items: list[dict] = []
    for rec in (meeting.recordings or []):
        mom = rec.mom_json or {}
        for ai in (mom.get("action_items") or []):
            if ai:
                items.append(ai)
    return items


def recording_sort_key(rec):
    """Deterministic chronological order for a meeting's recordings.

    `started_at` is the recency signal; `id` is only a STABLE TIEBREAKER so equal
    or duplicate timestamps don't reorder between runs. recording_id is a random
    UUID that does NOT encode order — it must never be the primary sort key, and
    callers must never infer sequence/position from it or from label numbers.
    Shared by list_recordings (live tool) and the memory-sync roster so both
    present sessions in the SAME order.
    """
    return (rec.started_at or datetime.min, str(rec.id))


async def list_recordings(
    session: AsyncSession, meeting_id: uuid.UUID
) -> list[dict]:
    """List a meeting's recordings as lightweight dicts, oldest first.

    Each entry = {recording_id, label, date, has_mom}, where label =
    `title or session_label` and date is the event date (falling back to the
    started_at date). Lets the chat agent map "Meeting 1"/ordinal/date →
    recording_id before reading that recording's MoM. Returns [] if the meeting
    is missing or has no recordings.
    """
    meeting = await get_meeting(session, meeting_id)
    if not meeting:
        return []
    recordings = sorted((meeting.recordings or []), key=recording_sort_key)
    out: list[dict] = []
    for rec in recordings:
        if rec.date:
            iso_date = rec.date.isoformat()
        elif rec.started_at:
            iso_date = rec.started_at.date().isoformat()
        else:
            iso_date = None
        out.append({
            "recording_id": str(rec.id),
            "label": rec.title or rec.session_label or "phiên",
            "date": iso_date,
            "has_mom": bool(rec.mom_json),
        })
    return out


async def get_recording_mom(
    session: AsyncSession, recording_id: uuid.UUID
) -> Optional[dict]:
    """Return one recording's stored MoM (recordings.mom_json), or None if the
    recording is missing or has no MoM yet."""
    recording = await session.get(Recording, recording_id)
    if not recording:
        return None
    return recording.mom_json


async def join_meeting_transcript(
    session: AsyncSession, meeting_id: uuid.UUID
) -> str:
    """Return all segments from all recordings of a meeting, joined as one string."""
    stmt = (
        select(TranscriptSegment)
        .join(Recording, Recording.id == TranscriptSegment.recording_id)
        .where(
            Recording.meeting_id == meeting_id,
            TranscriptSegment.is_deleted.is_(False),
        )
        .order_by(Recording.started_at, TranscriptSegment.seq)
    )
    segments = (await session.execute(stmt)).scalars().all()
    return "\n".join(s.text for s in segments)


# ─── Chat (Phase B2) ──────────────────────────────────────────────

async def create_chat_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    meeting_id: Optional[uuid.UUID] = None,
    title: Optional[str] = None,
) -> ChatSession:
    chat = ChatSession(user_id=user_id, meeting_id=meeting_id, title=title)
    session.add(chat)
    await session.flush()
    return chat


async def get_chat_session(
    session: AsyncSession, session_id: uuid.UUID
) -> Optional[ChatSession]:
    return await session.get(ChatSession, session_id)


async def list_chat_sessions_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[ChatSession]:
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.last_activity_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


async def add_chat_message(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    role: str,  # 'user' | 'agent' | 'tool' | 'system'
    content: dict,
    metadata: Optional[dict] = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        msg_metadata=metadata,
    )
    session.add(msg)
    # Touch last_activity_at
    chat = await session.get(ChatSession, session_id)
    if chat:
        chat.last_activity_at = datetime.now(timezone.utc)
    await session.flush()
    return msg


async def list_chat_messages(
    session: AsyncSession,
    session_id: uuid.UUID,
    limit: int = 50,
) -> Sequence[ChatMessage]:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def clear_chat_session(
    session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Delete a session's messages + pending actions in place (keeps the session
    row, its id, and meeting_id binding). Pending actions are deleted rather than
    status-flagged — a cleared session has no live interrupt to track and the
    status CHECK constraint has no 'cancelled' value."""
    await session.execute(
        delete(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    await session.execute(
        delete(PendingAction).where(PendingAction.session_id == session_id)
    )
    await session.flush()


async def delete_chat_session(
    session: AsyncSession, session_id: uuid.UUID
) -> None:
    """Hard-delete a chat session: its messages, pending actions, AND the
    session row itself. Distinct from clear_chat_session, which keeps the row.
    The LangGraph checkpoint thread is purged by the API layer (it owns the
    checkpointer handle), mirroring clear_session."""
    await session.execute(
        delete(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    await session.execute(
        delete(PendingAction).where(PendingAction.session_id == session_id)
    )
    await session.execute(
        delete(ChatSession).where(ChatSession.id == session_id)
    )
    await session.flush()


async def rename_chat_session(
    session: AsyncSession, session_id: uuid.UUID, title: str
) -> Optional[ChatSession]:
    """Set a chat session's title. Returns the session, or None if missing."""
    chat = await session.get(ChatSession, session_id)
    if not chat:
        return None
    chat.title = title
    await session.flush()
    return chat


# ─── Pending Actions (HITL) ───────────────────────────────────────

async def create_pending_action(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: str,
    tool_name: str,
    tool_args: dict,
    rationale: Optional[str] = None,
    checkpoint_id: Optional[str] = None,
) -> PendingAction:
    action = PendingAction(
        session_id=session_id,
        user_id=user_id,
        thread_id=thread_id,
        tool_name=tool_name,
        tool_args=tool_args,
        rationale=rationale,
        checkpoint_id=checkpoint_id,
        status="pending",
    )
    session.add(action)
    await session.flush()
    return action


async def get_pending_action(
    session: AsyncSession, action_id: uuid.UUID
) -> Optional[PendingAction]:
    return await session.get(PendingAction, action_id)


async def resolve_pending_action(
    session: AsyncSession,
    action_id: uuid.UUID,
    *,
    decision: str,  # 'approved' | 'rejected'
    edited_args: Optional[dict] = None,
    reason: Optional[str] = None,
) -> Optional[PendingAction]:
    action = await session.get(PendingAction, action_id)
    if not action:
        return None
    action.status = decision
    action.resolved_at = datetime.utcnow()
    action.resolution = {
        "action": decision,
        "edited_args": edited_args,
        "reason": reason,
    }
    if edited_args:
        action.tool_args = edited_args
    await session.flush()
    return action


async def mark_action_executed(
    session: AsyncSession,
    action_id: uuid.UUID,
    result: dict,
    success: bool = True,
) -> None:
    action = await session.get(PendingAction, action_id)
    if action:
        action.status = "executed" if success else "failed"
        if action.resolution is None:
            action.resolution = {}
        action.resolution = {**action.resolution, "execution_result": result}
        await session.flush()


# ─── Audit Log ────────────────────────────────────────────────────

async def log_audit(
    session: AsyncSession,
    *,
    user_id: Optional[uuid.UUID],
    session_id: Optional[uuid.UUID],
    action_type: str,
    tool_name: Optional[str] = None,
    tool_args: Optional[dict] = None,
    result: Optional[dict] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        session_id=session_id,
        action_type=action_type,
        tool_name=tool_name,
        tool_args=tool_args,
        result=result,
        success=success,
        error_msg=error_msg,
    )
    session.add(entry)
    await session.flush()
    return entry


# ─── Memory events (Sprint A) ─────────────────────────────────────

async def save_memory_event(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    meeting_id: uuid.UUID,
    event_type: str,
    text: str,
    topic: Optional[str] = None,
    speaker: Optional[str] = None,
    deadline: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> MemoryEventRow:
    event = MemoryEventRow(
        user_id=user_id,
        meeting_id=meeting_id,
        event_type=event_type,
        topic=topic,
        text=text,
        speaker=speaker,
        deadline=deadline,
        event_metadata=metadata,
    )
    session.add(event)
    await session.flush()
    return event


async def save_memory_events_bulk(
    session: AsyncSession,
    events: list[dict],
    *,
    user_id: uuid.UUID,
    meeting_id: uuid.UUID,
) -> int:
    """Save multiple events at once. events = [{event_type, text, topic?, ...}, ...].

    Embeds `text` in batch (1 API call for N events) before INSERT.
    Falls back to NULL embedding if embedding service unavailable.
    """
    from meeting.services.embedding import embed_batch

    valid = [ev for ev in events if ev.get("text") and ev.get("event_type")]
    if not valid:
        return 0

    # Batch embed (1 API call). Returns None per slot on failure.
    embeddings = embed_batch([ev["text"] for ev in valid])

    for ev, emb in zip(valid, embeddings):
        session.add(MemoryEventRow(
            user_id=user_id,
            meeting_id=meeting_id,
            event_type=ev["event_type"],
            text=ev["text"],
            topic=ev.get("topic"),
            speaker=ev.get("speaker"),
            deadline=ev.get("deadline"),
            event_metadata=ev.get("metadata"),
            embedding=emb,
        ))
    await session.flush()
    return len(valid)


async def retrieve_memory_events(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str = "",
    topic: Optional[str] = None,
    exclude_meeting_id: Optional[uuid.UUID] = None,
    meeting_id: Optional[uuid.UUID] = None,
    event_types: Optional[list[str]] = None,
    limit: int = 10,
) -> Sequence[MemoryEventRow]:
    """Hybrid retrieval: keyword FTS + vector similarity (RRF) + LLM rerank.

    Steps:
        1. FTS candidates: Postgres full-text search on `text` (top N)
        2. Vector candidates: cosine similarity on `embedding` (top N)
        3. Reciprocal Rank Fusion (RRF) merge → candidate pool (~2*limit)
        4. LLM re-rank pool → final top `limit`
        5. Falls back gracefully if embedding/LLM unavailable.

    Rerank can be disabled via env `RERANK_ENABLED=false` (skip step 4).
    """
    import os
    from sqlalchemy import func as sql_func
    from meeting.services.embedding import embed_text
    from meeting.services.reranker import rerank_with_llm

    def _base_filter(stmt):
        stmt = stmt.where(MemoryEventRow.user_id == user_id)
        if meeting_id:
            stmt = stmt.where(MemoryEventRow.meeting_id == meeting_id)
        if exclude_meeting_id:
            stmt = stmt.where(MemoryEventRow.meeting_id != exclude_meeting_id)
        if event_types:
            stmt = stmt.where(MemoryEventRow.event_type.in_(event_types))
        if topic:
            stmt = stmt.where(MemoryEventRow.topic.ilike(f"%{topic}%"))
        return stmt

    # No query → just recency-ordered (legacy behavior)
    if not query:
        stmt = _base_filter(select(MemoryEventRow))
        stmt = stmt.order_by(MemoryEventRow.created_at.desc()).limit(limit)
        return (await session.execute(stmt)).scalars().all()

    cand_pool = limit * 4  # over-fetch per side; RRF + rerank pick best

    # ── A. FTS candidates ─────────────────────────────────────
    fts_stmt = _base_filter(select(MemoryEventRow))
    fts_stmt = fts_stmt.where(
        sql_func.to_tsvector("simple", MemoryEventRow.text).op("@@")(
            sql_func.plainto_tsquery("simple", query)
        )
    ).order_by(MemoryEventRow.created_at.desc()).limit(cand_pool)
    fts_rows = list((await session.execute(fts_stmt)).scalars().all())

    # ── B. Vector candidates ──────────────────────────────────
    query_emb = embed_text(query)
    vec_rows: list[MemoryEventRow] = []
    if query_emb is not None:
        vec_stmt = _base_filter(select(MemoryEventRow))
        vec_stmt = vec_stmt.where(MemoryEventRow.embedding.is_not(None))
        vec_stmt = vec_stmt.order_by(
            MemoryEventRow.embedding.cosine_distance(query_emb)
        ).limit(cand_pool)
        vec_rows = list((await session.execute(vec_stmt)).scalars().all())

    # ── C. Reciprocal Rank Fusion ─────────────────────────────
    k = 60
    scores: dict[uuid.UUID, float] = {}
    row_map: dict[uuid.UUID, MemoryEventRow] = {}
    for rank, r in enumerate(fts_rows):
        scores[r.id] = scores.get(r.id, 0.0) + 1.0 / (k + rank)
        row_map[r.id] = r
    for rank, r in enumerate(vec_rows):
        scores[r.id] = scores.get(r.id, 0.0) + 1.0 / (k + rank)
        row_map[r.id] = r

    rrf_ranked_ids = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)

    # ── D. LLM rerank (optional, default OFF to avoid Qwen3 rate limits) ──
    # Set RERANK_ENABLED=true in .env to enable. RRF hybrid alone is usually
    # good enough; rerank adds ~3-5s + 1 LLM call per retrieval which can
    # trigger 429 when stacked behind the MoM-gen LLM calls.
    rerank_enabled = os.getenv("RERANK_ENABLED", "false").lower() == "true"
    rerank_pool = min(int(os.getenv("RERANK_POOL", "20")), len(rrf_ranked_ids))

    if rerank_enabled and rerank_pool > limit:
        # Build (id, text) tuples for top `rerank_pool` after RRF
        candidates = [
            (str(rid), row_map[rid].text) for rid in rrf_ranked_ids[:rerank_pool]
        ]
        ranked_str_ids = rerank_with_llm(query=query, candidates=candidates, top_k=limit)
        # Convert back to UUID and resolve to rows
        return [row_map[uuid.UUID(sid)] for sid in ranked_str_ids if uuid.UUID(sid) in row_map]

    # No rerank → return RRF top-limit
    return [row_map[i] for i in rrf_ranked_ids[:limit]]

