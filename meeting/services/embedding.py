"""Embedding helper — wraps VNG MaaS bge-m3 (or any OpenAI-compatible endpoint).

Used by `memory_service` to compute embeddings before INSERT and at query time
for hybrid retrieval (FTS + vector similarity).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_model: Optional[str] = None
_dim: int = 1024


def _get_client():
    """Lazy singleton client. Reads env on first call."""
    global _client, _model, _dim
    if _client is None:
        base_url = os.getenv("EMBED_BASE_URL")
        api_key = os.getenv("EMBED_API_KEY")
        if not base_url or not api_key:
            raise RuntimeError(
                "EMBED_BASE_URL / EMBED_API_KEY env vars required for embedding"
            )
        _client = OpenAI(api_key=api_key, base_url=base_url)
        _model = os.getenv("EMBED_MODEL", "bge-m3")
        _dim = int(os.getenv("EMBED_DIM", "1024"))
        logger.info(f"[embedding] init client model={_model} dim={_dim}")
    return _client, _model


def embed_text(text: str) -> Optional[list[float]]:
    """Get embedding for a single text. Returns None on failure (caller skips)."""
    if not text or not text.strip():
        return None
    try:
        client, model = _get_client()
        resp = client.embeddings.create(model=model, input=text)
        return resp.data[0].embedding
    except Exception as e:
        logger.warning(f"[embedding] failed for text[:60]={text[:60]!r}: {e}")
        return None


def embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Batch embed N texts. Returns list of vectors (None for failures).

    bge-m3 supports batching natively → 1 API call for N inputs is much
    faster than N sequential calls.
    """
    if not texts:
        return []
    try:
        client, model = _get_client()
        resp = client.embeddings.create(model=model, input=texts)
        # Response.data is ordered same as input
        return [d.embedding for d in resp.data]
    except Exception as e:
        logger.warning(f"[embedding] batch failed ({len(texts)} texts): {e}")
        return [None] * len(texts)
