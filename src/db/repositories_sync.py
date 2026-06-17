"""Sync mirrors of the repo functions Celery tasks need.

Why this exists:
    Celery workers run in a sync world. Forcing the async repo functions
    through `asyncio.run()` creates a new event loop per task, and the
    SQLAlchemy async pool's connections (bound to that loop via asyncpg)
    become unusable for the next task — the recurring "Future attached to
    a different loop" errors. Native sync SQLAlchemy via psycopg2 has no
    event loop, no binding, no recurring class of bug.

    FastAPI continues to use the async repo for HTTP concurrency. This
    file is a focused subset — only the functions Celery tasks call.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from src.db.models import (
    Meeting,
    Recording,
    TranscriptSegment,
    User,
)


def get_or_create_dev_user(session: Session) -> User:
    """Sync mirror of repositories.get_or_create_dev_user."""
    DEV_MS_OID = "dev-local-user"
    user = session.execute(
        select(User).where(User.ms_oid == DEV_MS_OID)
    ).scalar_one_or_none()
    if user:
        return user
    user = User(
        ms_oid=DEV_MS_OID,
        email="user@vng.com.vn",
        display_name="User",
    )
    session.add(user)
    session.flush()
    return user


def get_recording(session: Session, recording_id: uuid.UUID) -> Optional[Recording]:
    """Sync mirror of repositories.get_recording."""
    return session.get(Recording, recording_id)


def get_meeting(session: Session, meeting_id: uuid.UUID) -> Optional[Meeting]:
    """Sync mirror — eager-loads recordings + segments (matches async version)."""
    return session.execute(
        select(Meeting)
        .where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
        .options(selectinload(Meeting.recordings).selectinload(Recording.segments))
    ).scalar_one_or_none()


def join_recording_transcript(session: Session, recording_id: uuid.UUID) -> str:
    """Sync mirror of repositories.join_recording_transcript.

    Same formatting rules: prefix with `[mm:ss] SPEAKER_NN:` when speaker
    is set, change only emit the speaker tag on speaker transitions.
    """
    segments = session.execute(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.recording_id == recording_id,
            TranscriptSegment.is_deleted.is_(False),
        )
        .order_by(TranscriptSegment.seq)
    ).scalars().all()
    out: list[str] = []
    last_speaker: Optional[str] = None
    for s in segments:
        spk = (s.speaker or "").strip() or None
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


def save_recording_phonetic(
    session: Session,
    recording_id: uuid.UUID,
    phonetic_json: dict,
) -> None:
    """Sync mirror of repositories.save_recording_phonetic."""
    rec = session.get(Recording, recording_id)
    if not rec:
        return
    rec.phonetic_examples_json = phonetic_json
    flag_modified(rec, "phonetic_examples_json")
    session.flush()


def match_clusters_to_names_sync(
    session: Session,
    *,
    user_id: uuid.UUID,
    speaker_embeddings: Optional[dict],
    threshold: float = 0.45,
) -> dict[str, str]:
    """Sync version of speaker_matcher.match_clusters_to_names.

    Re-implements bulk_match against the voiceprints table inline so we
    don't have to mirror the entire repositories_voiceprint module. Same
    cosine-distance threshold (0.45 ≈ 0.55 cosine similarity = strong match).
    """
    if not speaker_embeddings:
        return {}
    from src.db.models import SpeakerVoiceprint
    out: dict[str, str] = {}
    for cluster_id, emb in speaker_embeddings.items():
        if not emb:
            continue
        # pgvector cosine distance — `<=>` operator. Lower is more similar.
        row = session.execute(
            select(
                SpeakerVoiceprint.name,
                SpeakerVoiceprint.embedding.cosine_distance(emb).label("dist"),
            )
            .where(SpeakerVoiceprint.user_id == user_id)
            .order_by("dist")
            .limit(1)
        ).first()
        if row and row.dist is not None and row.dist <= threshold:
            out[cluster_id] = row.name
    return out
