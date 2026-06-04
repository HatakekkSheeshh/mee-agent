"""Repository helpers for SpeakerVoiceprint (zero-shot speaker ID).

Kept in a separate module to avoid bloating repositories.py — re-exported
there if needed. Cosine distance via pgvector's <=> operator.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db.models import SpeakerVoiceprint


async def save_voiceprint(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    embedding: list[float],
) -> SpeakerVoiceprint:
    """Upsert: if (user_id, name) exists → update embedding (running mean) +
    bump sample_count + last_seen_at. Otherwise insert new row.

    Running-mean update keeps the embedding stable as more samples accumulate
    for the same person, instead of just overwriting with the latest sample.
    """
    if len(embedding) != 256:
        raise ValueError(f"Expected 256-dim embedding, got {len(embedding)}")

    stmt = select(SpeakerVoiceprint).where(
        SpeakerVoiceprint.user_id == user_id,
        SpeakerVoiceprint.name == name,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        n = existing.sample_count
        # Running mean: avg = (n * old + new) / (n + 1)
        merged = [
            (n * old + new) / (n + 1)
            for old, new in zip(existing.embedding, embedding)
        ]
        existing.embedding = merged
        existing.sample_count = n + 1
        existing.last_seen_at = datetime.utcnow()
        await session.flush()
        return existing

    vp = SpeakerVoiceprint(
        user_id=user_id,
        name=name,
        embedding=embedding,
        sample_count=1,
        last_seen_at=datetime.utcnow(),
    )
    session.add(vp)
    await session.flush()
    return vp


async def find_similar_voiceprint(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    embedding: list[float],
    threshold: float = 0.30,  # cosine DISTANCE (lower = closer); ~0.3 ≈ 0.7 similarity
    limit: int = 1,
) -> list[tuple[SpeakerVoiceprint, float]]:
    """Find top-K voiceprints closest to `embedding` for the given user.

    Returns [(voiceprint, distance), ...] sorted by distance ASC.
    Caller decides whether to accept based on threshold.
    """
    if len(embedding) != 256:
        raise ValueError(f"Expected 256-dim embedding, got {len(embedding)}")

    # pgvector: `<=>` returns cosine distance in [0, 2]. Lower = more similar.
    distance_expr = SpeakerVoiceprint.embedding.cosine_distance(embedding)
    stmt = (
        select(SpeakerVoiceprint, distance_expr.label("distance"))
        .where(
            SpeakerVoiceprint.user_id == user_id,
            distance_expr <= threshold,
        )
        .order_by("distance")
        .limit(limit)
    )
    result = (await session.execute(stmt)).all()
    return [(row.SpeakerVoiceprint, float(row.distance)) for row in result]


async def list_voiceprints(
    session: AsyncSession, user_id: uuid.UUID
) -> Sequence[SpeakerVoiceprint]:
    stmt = (
        select(SpeakerVoiceprint)
        .where(SpeakerVoiceprint.user_id == user_id)
        .order_by(SpeakerVoiceprint.last_seen_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


async def rename_voiceprint(
    session: AsyncSession,
    voiceprint_id: uuid.UUID,
    user_id: uuid.UUID,
    new_name: str,
) -> Optional[SpeakerVoiceprint]:
    vp = await session.get(SpeakerVoiceprint, voiceprint_id)
    if not vp or vp.user_id != user_id:
        return None
    new_name = new_name.strip()
    if not new_name:
        return vp
    vp.name = new_name
    await session.flush()
    return vp


async def delete_voiceprint(
    session: AsyncSession, voiceprint_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    vp = await session.get(SpeakerVoiceprint, voiceprint_id)
    if not vp or vp.user_id != user_id:
        return False
    await session.delete(vp)
    await session.flush()
    return True


async def bulk_match(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cluster_embeddings: dict[str, list[float]],
    threshold: float = 0.30,
) -> dict[str, tuple[str, float]]:
    """Match many cluster_id → name using the user's voiceprint DB.

    Args:
        cluster_embeddings: {"SPEAKER_00": [...256 floats...], ...}

    Returns:
        {"SPEAKER_00": (name, distance), ...} — only includes clusters that
        matched. Unmatched clusters are absent.
    """
    out: dict[str, tuple[str, float]] = {}
    for cluster_id, emb in cluster_embeddings.items():
        matches = await find_similar_voiceprint(
            session, user_id=user_id, embedding=emb, threshold=threshold, limit=1,
        )
        if matches:
            vp, dist = matches[0]
            out[cluster_id] = (vp.name, dist)
    return out
