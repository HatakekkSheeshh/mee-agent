"""
Repository layer — DB access patterns for Mee.

Pattern: each function takes an AsyncSession + parameters, returns ORM objects.
No business logic here — just data access.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional, Sequence

from sqlalchemy import delete, select
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
    TranscriptSegment,
    User,
)


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


# ─── Meeting ──────────────────────────────────────────────────────

async def create_meeting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    purpose: str = "",
    venue: str = "",
    meeting_date: Optional[date] = None,
    chaired_by: str = "",
    noted_by: str = "",
    attendees: Optional[list] = None,
) -> Meeting:
    meeting = Meeting(
        user_id=user_id,
        title=title or "Untitled meeting",
        purpose=purpose or None,
        venue=venue or None,
        date=meeting_date,
        chaired_by=chaired_by or None,
        noted_by=noted_by or None,
        attendees=attendees,
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
    """List meetings where user is an active member (any role)."""
    stmt = (
        select(Meeting)
        .join(MeetingMember, MeetingMember.meeting_id == Meeting.id)
        .where(
            MeetingMember.user_id == user_id,
            MeetingMember.revoked_at.is_(None),
            Meeting.deleted_at.is_(None),
        )
        .order_by(Meeting.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


async def save_mom(
    session: AsyncSession, meeting_id: uuid.UUID, mom_json: dict
) -> None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.mom_json = mom_json
        await session.flush()


# ─── Recording ────────────────────────────────────────────────────

async def start_recording(
    session: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    session_label: Optional[str] = None,
) -> Recording:
    recording = Recording(
        meeting_id=meeting_id,
        session_label=session_label,
        status="recording",
    )
    session.add(recording)
    await session.flush()
    return recording


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
) -> TranscriptSegment:
    segment = TranscriptSegment(
        recording_id=recording_id,
        seq=seq,
        original_text=original_text,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
        speaker=speaker,
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
        chat.last_activity_at = datetime.utcnow()
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
    """Save multiple events at once. events = [{event_type, text, topic?, speaker?, deadline?}, ...]"""
    count = 0
    for ev in events:
        if not ev.get("text") or not ev.get("event_type"):
            continue
        session.add(MemoryEventRow(
            user_id=user_id,
            meeting_id=meeting_id,
            event_type=ev["event_type"],
            text=ev["text"],
            topic=ev.get("topic"),
            speaker=ev.get("speaker"),
            deadline=ev.get("deadline"),
            event_metadata=ev.get("metadata"),
        ))
        count += 1
    if count:
        await session.flush()
    return count


async def retrieve_memory_events(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str = "",
    topic: Optional[str] = None,
    exclude_meeting_id: Optional[uuid.UUID] = None,
    event_types: Optional[list[str]] = None,
    limit: int = 10,
) -> Sequence[MemoryEventRow]:
    """
    Retrieve memory events for a user, optionally filtered by topic/query.

    Use cases:
        - "What did team commit to last week?" → event_types=['commitment'], limit=5
        - "Any blockers on deploy?" → query='deploy', event_types=['blocker']
        - Pre-meeting context → topic matched + recency
    """
    stmt = select(MemoryEventRow).where(MemoryEventRow.user_id == user_id)
    if exclude_meeting_id:
        stmt = stmt.where(MemoryEventRow.meeting_id != exclude_meeting_id)
    if event_types:
        stmt = stmt.where(MemoryEventRow.event_type.in_(event_types))
    if topic:
        # Keyword match topic
        stmt = stmt.where(MemoryEventRow.topic.ilike(f"%{topic}%"))
    if query:
        # Postgres full-text search on text column
        from sqlalchemy import func as sql_func, text as sql_text
        stmt = stmt.where(
            sql_func.to_tsvector("simple", MemoryEventRow.text).op("@@")(
                sql_func.plainto_tsquery("simple", query)
            )
        )
    stmt = stmt.order_by(MemoryEventRow.created_at.desc()).limit(limit)
    return (await session.execute(stmt)).scalars().all()
