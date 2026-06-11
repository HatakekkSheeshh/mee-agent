"""
SQLAlchemy 2 ORM models for Mee Meeting Agent — Phase A.

5 core tables (per DB Schema 3-cap v0.3):
    users → meetings → recordings → transcript_segments
                  │
                  └── meeting_members (sharing)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from meeting.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Microsoft Object ID (sub from O365 JWT). Nullable so MockProvider users
    # don't need fake ones. Real-O365 users populate this in /auth/callback.
    ms_oid: Mapped[Optional[str]] = mapped_column(Text, unique=True)
    ms_tenant_id: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)  # AES-256 encrypted
    # True once user records the enrollment phrase post-login. Matching
    # voiceprint row lives in `voiceprints` with label="enrollment".
    voice_enrolled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    meetings: Mapped[list["Meeting"]] = relationship(back_populates="creator")
    memberships: Mapped[list["MeetingMember"]] = relationship(
        foreign_keys="MeetingMember.user_id", back_populates="user"
    )

    __table_args__ = (Index("idx_users_email", "email"),)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Project (grouping) name shown in sidebar. Per-meeting event metadata
    # (date, attendees, chair, purpose...) now lives on `recordings` — a
    # Meeting is purely a grouping mechanism + project-wide defaults.
    title: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(Text)
    # Project default vocab (e.g. "segmentation, CNN, deploy, API"). At
    # runtime, cleaner LLM + Whisper prompt APPEND this with the recording's
    # own vocab_hints (per-session additions).
    vocab_hints: Mapped[Optional[str]] = mapped_column(Text)
    # Project default STT + LLM model choices. Logical IDs from model_registry
    # ("whisper" / "phowhisper" / "gemma" / "qwen" / "gpt-oss"). NULL falls
    # back to DEFAULT_STT/DEFAULT_LLM. Recording-level fields override these.
    stt_model: Mapped[Optional[str]] = mapped_column(Text)
    llm_model: Mapped[Optional[str]] = mapped_column(Text)
    # MoM generation language ("vi" / "en"). NULL → request body (UI lang) → "vi".
    mom_language: Mapped[Optional[str]] = mapped_column(Text)
    # Project-level summary aggregating decisions across all recordings (timeline).
    # Per-recording MoMs live on recordings.mom_json.
    project_summary_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    creator: Mapped[User] = relationship(back_populates="meetings")
    recordings: Mapped[list["Recording"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )
    members: Mapped[list["MeetingMember"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_meetings_user_created", "user_id", "created_at"),
    )


class MeetingMember(Base):
    __tablename__ = "meeting_members"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    meeting: Mapped[Meeting] = relationship(back_populates="members")
    user: Mapped[User] = relationship(
        foreign_keys=[user_id], back_populates="memberships"
    )

    __table_args__ = (
        PrimaryKeyConstraint("meeting_id", "user_id"),
        CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')", name="ck_members_role"
        ),
        Index("idx_members_meeting", "meeting_id"),
    )


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Short label shown in sidebar — fallback when `title` not set.
    session_label: Mapped[Optional[str]] = mapped_column(Text)
    # Per-meeting event metadata (moved from meetings table in migration 0012).
    # Each recording is an actual meeting event with its own context.
    title: Mapped[Optional[str]] = mapped_column(Text)
    purpose: Mapped[Optional[str]] = mapped_column(Text)
    date: Mapped[Optional[date]] = mapped_column(Date)
    venue: Mapped[Optional[str]] = mapped_column(Text)
    chaired_by: Mapped[Optional[str]] = mapped_column(Text)
    noted_by: Mapped[Optional[str]] = mapped_column(Text)
    attendees: Mapped[Optional[list]] = mapped_column(JSONB)  # [{name, dept, title}]
    # Per-recording vocab additions. At runtime APPENDED to meeting.vocab_hints
    # (project default), giving 2-tier vocab. NULL = use project default only.
    vocab_hints: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        Text, default="recording", server_default="recording"
    )
    audio_path: Mapped[Optional[str]] = mapped_column(Text)
    # Cached LLM clean output ({segments: [{speaker, text, tags}]}); null until first clean run.
    clean_segments: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Per-recording MoM (biên bản phiên họp). Each recording has its own MoM.
    # Project-level aggregate lives on meetings.project_summary_json.
    mom_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    # 256-dim voice embeddings keyed by pyannote cluster id (SPEAKER_00, ...).
    # Populated when audio is uploaded → phowhisper-server runs pyannote
    # embedding inference. Used by speaker_matcher in the Clean step.
    speaker_embeddings: Mapped[Optional[dict]] = mapped_column(JSONB)
    # PhoWhisper-diarized transcript with SPEAKER_NN prefixes, concatenated as
    # "SPEAKER_00: text\nSPEAKER_01: text\n…". For live-record path this is
    # populated by post_record_diarize and is the AUTHORITATIVE input for
    # the cleaner LLM (preferred over join_recording_transcript which lacks
    # speaker tags). NULL = no diarization run yet for this recording.
    diarized_text: Mapped[Optional[str]] = mapped_column(Text)
    # Per-recording STT + LLM model overrides (migration 0014). NULL = inherit
    # from meeting (project default), which itself falls back to registry default.
    stt_model: Mapped[Optional[str]] = mapped_column(Text)
    llm_model: Mapped[Optional[str]] = mapped_column(Text)
    # MoM language override (migration 0015). NULL = inherit from meeting.
    mom_language: Mapped[Optional[str]] = mapped_column(Text)
    # LLM-generated phonetic mappings derived from vocab_hints (migration 0013).
    # Shape: {"mappings": [{"wrong": "...", "correct": "..."}], "vocab_hash": "sha256",
    #         "generated_at_ms": int}. Cleaner injects mappings as few-shot
    # examples — replaces the static hardcoded list. Regenerated when
    # vocab_hints changes (vocab_hash mismatch).
    phonetic_examples_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Filesystem paths to 3s voice sample WAVs per pyannote cluster (migration
    # 0016). Shape: {"SPEAKER_00": "output/<rid>/spk_SPEAKER_00.wav", ...}. Set
    # by /diarize-result when sample_audio_b64 is provided. Served via
    # GET /api/recordings/{id}/speaker-sample/{label} so SpeakerMapper can
    # play a clip before the user confirms the speaker name.
    speaker_sample_paths: Mapped[Optional[dict]] = mapped_column(JSONB)

    meeting: Mapped[Meeting] = relationship(back_populates="recordings")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="recording", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_recordings_meeting_time", "meeting_id", "started_at"),
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recordings.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    end_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    speaker: Mapped[Optional[str]] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_text: Mapped[Optional[str]] = mapped_column(Text)
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    edited_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    recording: Mapped[Recording] = relationship(back_populates="segments")

    __table_args__ = (
        Index(
            "idx_segments_recording_seq",
            "recording_id",
            "seq",
            postgresql_where=(is_deleted.is_(False)),
        ),
    )

    @property
    def text(self) -> str:
        """Effective text — edited if available, else original (COALESCE pattern)."""
        return self.edited_text if self.edited_text is not None else self.original_text


# ═══════════════════════════════════════════════════════════════════════
# Phase B2 — Chat & HITL tables
# ═══════════════════════════════════════════════════════════════════════

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="SET NULL")
    )
    title: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )
    pending_actions: Mapped[list["PendingAction"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # user/agent/tool/system
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    msg_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'agent', 'tool', 'system')",
            name="ck_messages_role",
        ),
    )


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)  # LangGraph thread to resume
    checkpoint_id: Mapped[Optional[str]] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_args: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[Optional[dict]] = mapped_column(JSONB)

    session: Mapped[ChatSession] = relationship(back_populates="pending_actions")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executed', 'failed')",
            name="ck_pending_actions_status",
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL")
    )
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[Optional[str]] = mapped_column(Text)
    tool_args: Mapped[Optional[dict]] = mapped_column(JSONB)
    result: Mapped[Optional[dict]] = mapped_column(JSONB)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ═══════════════════════════════════════════════════════════════════════
# Sprint A — Memory events for cross-meeting context
# ═══════════════════════════════════════════════════════════════════════

class MemoryEventRow(Base):
    """
    Persistent memory events extracted from MoMs.
    Replaces stub MemoryService.save() — now writes to DB for cross-meeting context.
    """
    __tablename__ = "memory_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[Optional[str]] = mapped_column(Text)
    deadline: Mapped[Optional[str]] = mapped_column(Text)
    event_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Semantic embedding (bge-m3, 1024 dim). NULL if not yet computed.
    embedding: Mapped[Optional[list[float]]] = mapped_column(
        __import__("pgvector.sqlalchemy", fromlist=["Vector"]).Vector(1024),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('action_item', 'decision', 'commitment', 'blocker', 'update', 'summary')",
            name="ck_memory_events_type",
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase Speaker-ID — Cross-meeting voiceprints
# ═══════════════════════════════════════════════════════════════════════

class SpeakerVoiceprint(Base):
    """Per-user voiceprint dictionary for zero-shot speaker identification.

    Each row binds a person's name to their pyannote voice embedding (256-dim).
    When transcribing a new meeting, the matcher service computes cluster
    embeddings via pyannote/embedding inference and cosine-searches this table
    to pre-map SPEAKER_NN → real names. Updated when the user manually labels
    a speaker in CleanEditor.
    """
    __tablename__ = "speaker_voiceprints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 256-dim embedding (pyannote/embedding output). pgvector handles cosine.
    embedding: Mapped[list[float]] = mapped_column(
        __import__("pgvector.sqlalchemy", fromlist=["Vector"]).Vector(256),
        nullable=False,
    )
    sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_voiceprints_user_name", "user_id", "name", unique=True),
    )
