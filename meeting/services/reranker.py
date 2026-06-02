"""LLM-as-reranker — re-score (query, document) pairs using the chat LLM.

VNG MaaS doesn't expose a dedicated reranker (bge-reranker-*) endpoint, so we
fall back to prompting the chat LLM (Qwen3-5-27b) to score relevance. Slower
than a cross-encoder but zero extra deps + works with current infra.

Usage:
    from meeting.services.reranker import rerank_with_llm
    top_ids = rerank_with_llm(
        query="Sprint Review",
        candidates=[(id1, "Tuấn deploy v1..."), (id2, "Linh chốt budget..."), ...],
        top_k=5,
    )
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


RERANK_PROMPT = """Bạn là re-ranker. Cho query + list documents, score mỗi doc theo độ liên quan với query (0-10).

Query: {query}

Documents (index + nội dung):
{docs}

Output CHỈ JSON array (không markdown fence, không giải thích), schema:
[{{"index": 0, "score": 8}}, {{"index": 1, "score": 3}}, ...]

Lưu ý:
- score 0 = hoàn toàn không liên quan
- score 10 = liên quan trực tiếp
- Phải có đủ {n} entries, mỗi index 0..{max_idx}
"""


def _get_llm_client():
    """Reuse existing LLM env vars (same as note_generator)."""
    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
    )
    model = os.getenv("LLM_MODEL", "qwen/qwen3-5-27b")
    return client, model


def rerank_with_llm(
    query: str,
    candidates: list[tuple[str, str]],
    top_k: int = 5,
    timeout: int = 30,
    max_doc_chars: int = 200,
) -> list[str]:
    """Re-rank candidates by LLM relevance score.

    Args:
        query: search query string
        candidates: list of (id, text) tuples — typically top-N from hybrid search
        top_k: number to return after re-ranking
        timeout: LLM timeout
        max_doc_chars: truncate each doc to this length in prompt (saves tokens)

    Returns:
        Ordered list of candidate IDs (most relevant first), max length=top_k.
        Falls back to original order if LLM call fails.
    """
    if not candidates or not query.strip():
        return [cid for cid, _ in candidates[:top_k]]

    if len(candidates) <= top_k:
        return [cid for cid, _ in candidates]  # nothing to rerank

    # Build prompt
    n = len(candidates)
    docs_str = "\n".join(
        f"[{i}] {(text or '')[:max_doc_chars]}"
        for i, (_, text) in enumerate(candidates)
    )
    prompt = RERANK_PROMPT.format(query=query, docs=docs_str, n=n, max_idx=n - 1)

    try:
        client, model = _get_llm_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            timeout=timeout,
            # Same Qwen3 thinking-off flag as MoM/clean prompts
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        output = (resp.choices[0].message.content or "").strip()

        # Strip code fences if present
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        scores = json.loads(output)
        if not isinstance(scores, list):
            raise ValueError("expected JSON array")

        # Sort by score desc, keep top_k
        sorted_entries = sorted(
            (e for e in scores if isinstance(e, dict) and "index" in e and "score" in e),
            key=lambda e: e.get("score", 0),
            reverse=True,
        )[:top_k]

        ranked_ids: list[str] = []
        for e in sorted_entries:
            idx = e["index"]
            if 0 <= idx < n:
                ranked_ids.append(candidates[idx][0])

        # Pad with leftover candidates if LLM returned fewer than top_k
        seen = set(ranked_ids)
        for cid, _ in candidates:
            if len(ranked_ids) >= top_k:
                break
            if cid not in seen:
                ranked_ids.append(cid)
                seen.add(cid)

        logger.info(
            f"[reranker] reranked {n} → top {len(ranked_ids)} via LLM"
        )
        return ranked_ids

    except Exception as e:
        logger.warning(
            f"[reranker] LLM call failed ({e}); falling back to original order"
        )
        return [cid for cid, _ in candidates[:top_k]]
