"""Speaker matcher — bridges cluster_embeddings ⇄ voiceprint DB.

Called by the Clean step:
    1. Load recording.speaker_embeddings (set by PhoWhisper server at upload)
    2. For each cluster, cosine-search the user's voiceprint DB
    3. Return {SPEAKER_NN: (name, distance)} for clusters that matched
       above the similarity threshold. Unmatched clusters are absent.

The cleaner LLM then takes these pre-mappings + attendees list and infers
names for the remaining unmapped clusters via context cues (intros,
self-references, etc.).
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories_voiceprint import bulk_match

logger = logging.getLogger(__name__)


async def match_clusters_to_names(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    speaker_embeddings: Optional[dict],
    threshold: float = 0.45,
) -> dict[str, str]:
    """Resolve cluster ids → known names from the user's voiceprint DB.

    Args:
        speaker_embeddings: {"SPEAKER_00": [...256 floats...], ...} or None
        threshold: cosine distance cutoff (0 = identical, 2 = opposite).
                   ~0.45 ≈ 0.55 cosine similarity → strong match.

    Returns:
        {"SPEAKER_00": "Linh", "SPEAKER_01": "Tuấn"} — only includes matches.
        Empty dict if no embeddings or no matches.
    """
    if not speaker_embeddings:
        return {}

    matches = await bulk_match(
        session,
        user_id=user_id,
        cluster_embeddings=speaker_embeddings,
        threshold=threshold,
    )
    # bulk_match returns {cluster_id: (name, distance)} — strip distance
    out = {cid: name for cid, (name, _dist) in matches.items()}
    if out:
        logger.info(
            f"Voice-matched {len(out)}/{len(speaker_embeddings)} clusters: {out}"
        )
    return out
