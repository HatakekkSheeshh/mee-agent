"""Phonetic example generator — dynamic few-shot for the cleaner LLM.

PROBLEM
  Whisper ASR mistranscribes English tech terms as Vietnamese phonetic
  nonsense ("convolution" → "công vô lu sần"). The cleaner LLM fixes
  these, but only if it has *examples* showing how the rewrite works.
  Hardcoding 3-5 examples in the prompt doesn't scale across domains
  (ML, devops, healthcare, legal) — each one mistranscribes differently.

SOLUTION
  For each recording, run a one-shot LLM call that turns the user's
  `vocab_hints` ("ResNet, Conv2D, Transfer Learning, …") into a list of
  likely VN mistranscriptions:
      [{"wrong": "rét nét", "correct": "ResNet"},
       {"wrong": "con vô tu đi", "correct": "Conv2D"}, ...]
  Cache in `recording.phonetic_examples_json` keyed by a hash of the
  vocab string so we only regenerate when vocab actually changes.

  The cleaner prompt then injects these mappings in the same slot where
  the old hardcoded examples lived → recall ↑ on rare/domain-specific
  terminology without code edits.

This module is sync (uses `requests`-style OpenAI SDK like transcript_cleaner)
so callers must wrap in `asyncio.to_thread` from async contexts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


PHONETIC_GEN_PROMPT = """Bạn là chuyên gia phonetics tiếng Việt - tiếng Anh.

## Nhiệm vụ
Cho danh sách thuật ngữ kỹ thuật tiếng Anh dưới đây. Với MỖI term, sinh
2-3 cách phát âm SAI mà Whisper ASR tiếng Việt thường nhầm khi:
  - Người Việt nói term tiếng Anh với accent
  - Audio nhiễu, không rõ
  - Whisper decoder bias về phonemes tiếng Việt

## Quy tắc sinh phonetic
1. Tách English thành syllable (vd "convolution" = "con-vo-lu-tion")
2. Chuyển mỗi syllable sang âm tiết Việt gần nhất theo cách người Việt
   thường phát âm (vd "tion" → "sần" / "shần" / "sân")
3. Có thể có dấu thanh hoặc không (dấu sắc, huyền, hỏi, ngã, nặng)
4. Kết quả PHẢI là cụm từ Việt VÔ NGHĨA — không trùng từ tiếng Việt thông dụng
5. Mỗi term tối đa 3 variant, ưu tiên variant phổ biến nhất

## Ví dụ chuẩn (học pattern từ đây)
- "convolution" → ["công vô lu sần", "con vô lút", "công vu lút"]
- "segmentation" → ["chất manh tây sành", "séc men tây sần"]
- "ResNet" → ["rét nét", "rest nét", "rê snét"]
- "Conv2D" → ["công vô tu đi", "con vô hai đi"]
- "deploy" → ["đi pờ lôi", "đê plôi"]
- "ticket" → ["tích cốt", "tích két"]
- "Transfer Learning" → ["trên sờ phơ lơ ninh", "trans phơ lơ ning"]
- "pooling" → ["pu linh", "boo linh", "boolean"]

## Vocab cần xử lý
{vocab}

## Output Schema (BẮT BUỘC tuân thủ)
Trả về CHỈ JSON hợp lệ (không markdown fence, không giải thích, không thêm field):

{{
  "mappings": [
    {{"wrong": "rét nét",            "correct": "ResNet"}},
    {{"wrong": "rê snét",            "correct": "ResNet"}},
    {{"wrong": "công vô tu đi",      "correct": "Conv2D"}},
    {{"wrong": "tran sờ phơ lơ ninh","correct": "Transfer Learning"}}
  ]
}}

QUAN TRỌNG về field names:
- "wrong"   = chuỗi phonetic VIỆT vô nghĩa (cụm Whisper hay nhầm)
- "correct" = ORIGINAL English term (PHẢI giống y nguyên trong vocab list,
              KHÔNG được là phonetic khác)
- Mỗi variant phonetic = 1 entry RIÊNG trong mảng (flat list, KHÔNG nested)
- Mỗi term sinh 2-3 variant → tổng số entry = 2-3× số term"""


def _vocab_hash(vocab: str) -> str:
    """Stable hash for cache invalidation. Normalises whitespace + case."""
    norm = re.sub(r"\s+", " ", vocab.strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def needs_regeneration(
    current_vocab: str, cached: Optional[dict]
) -> bool:
    """Decide if we should call LLM to (re)generate phonetic mappings."""
    if not current_vocab or not current_vocab.strip():
        return False  # no vocab → nothing to generate
    if not cached:
        return True
    return cached.get("vocab_hash") != _vocab_hash(current_vocab)


def generate_phonetic_mappings(
    vocab: str,
    *,
    model: Optional[str] = None,
    llm_profile: Optional[dict] = None,
    timeout: int = 60,
    use_pool: bool = True,
) -> dict:
    """Run 1 LLM call to turn vocab into phonetic mappings.

    Args:
        vocab: comma-separated tech terms ("ResNet, Conv2D, …")
        model: override LLM_MODEL env var
        timeout: LLM timeout in seconds
        use_pool: when True (default), consult the global vocab pool first.
                  Terms already covered there are skipped — the LLM is asked
                  ONLY about new terms. If all are covered, no LLM call is
                  made. Generated mappings are also pushed back into the pool
                  so future meetings re-use them. Disable for forced regen.

    Returns the dict to store at `recording.phonetic_examples_json`:
        {"mappings": [{"wrong":..., "correct":...}, ...],
         "vocab_hash": "...",
         "generated_at_ms": int,
         "from_pool": int,   # how many came from the pool (no LLM cost)
         "from_llm": int}    # how many came from this LLM call

    On any failure returns {"mappings": [], "vocab_hash": "...", "error": "..."}
    — the cleaner falls back gracefully to no examples in that case.
    """
    vocab = (vocab or "").strip()
    if not vocab:
        return {"mappings": [], "vocab_hash": "", "generated_at_ms": int(time.time() * 1000)}

    # ─── Pool short-circuit: skip LLM for terms already covered ───
    pool_mappings: list[dict] = []
    llm_vocab = vocab
    if use_pool:
        try:
            from meeting.vocab_store import (
                get_corrections_for_vocab, terms_without_pool_corrections,
            )
            pool_mappings = get_corrections_for_vocab(vocab)
            uncovered = terms_without_pool_corrections(vocab)
            if not uncovered:
                # Whole vocab already in pool — zero LLM cost.
                logger.info(
                    f"[phonetic_generator] all {len(vocab.split(','))} terms "
                    f"covered by pool → {len(pool_mappings)} mappings reused"
                )
                return {
                    "mappings": pool_mappings,
                    "vocab_hash": _vocab_hash(vocab),
                    "generated_at_ms": int(time.time() * 1000),
                    "from_pool": len(pool_mappings),
                    "from_llm": 0,
                }
            llm_vocab = ", ".join(uncovered)
            logger.info(
                f"[phonetic_generator] pool covers {len(pool_mappings)} mappings; "
                f"generating new for {len(uncovered)} uncovered terms"
            )
        except Exception as e:
            logger.warning(f"[phonetic_generator] pool lookup failed (non-fatal): {e}")
            pool_mappings = []
            llm_vocab = vocab

    if llm_profile:
        client = OpenAI(
            api_key=llm_profile.get("api_key") or "",
            base_url=llm_profile.get("base_url") or "",
        )
        mdl = model or llm_profile.get("model") or os.getenv("LLM_MODEL", "google/gemma-4-31b-it")
    else:
        client = OpenAI(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", ""),
        )
        mdl = model or os.getenv("LLM_MODEL", "google/gemma-4-31b-it")

    prompt = PHONETIC_GEN_PROMPT.format(vocab=llm_vocab)

    extra_kwargs: dict = {}
    if "qwen" in mdl.lower():
        extra_kwargs["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    try:
        resp = client.chat.completions.create(
            model=mdl,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,  # ~50 mappings worst case
            timeout=timeout,
            **extra_kwargs,
        )
        output = (resp.choices[0].message.content or "").strip()
        # Strip thinking tags + code fences
        output = re.sub(r"<think>.*?</think>", "", output, flags=re.DOTALL | re.IGNORECASE).strip()
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        parsed = json.loads(output)
        mappings = parsed.get("mappings", [])
        if not isinstance(mappings, list):
            raise ValueError("mappings is not a list")

        # Sanitise — each must have wrong + correct, both non-empty strings.
        clean: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for m in mappings:
            if not isinstance(m, dict):
                continue
            w = str(m.get("wrong", "")).strip()
            c = str(m.get("correct", "")).strip()
            if not w or not c:
                continue
            key = (w.lower(), c.lower())
            if key in seen:
                continue
            seen.add(key)
            clean.append({"wrong": w, "correct": c})

        # Bulk-save new mappings to the global pool so future meetings reuse.
        if use_pool and clean:
            try:
                from meeting.vocab_store import bulk_add_corrections
                added = bulk_add_corrections(clean)
                logger.info(
                    f"[phonetic_generator] saved {added} new mappings to pool"
                )
            except Exception as e:
                logger.warning(f"[phonetic_generator] pool save failed: {e}")

        merged = pool_mappings + clean
        # Deduplicate on `wrong` — pool entries win when both have the same key.
        seen_keys: set[str] = set()
        deduped: list[dict] = []
        for m in merged:
            w = m.get("wrong", "").lower()
            if w in seen_keys:
                continue
            seen_keys.add(w)
            deduped.append(m)

        logger.info(
            f"[phonetic_generator] vocab='{vocab[:80]}…' → "
            f"{len(deduped)} mappings ({len(pool_mappings)} from pool, "
            f"{len(clean)} from LLM)"
        )
        return {
            "mappings": deduped,
            "vocab_hash": _vocab_hash(vocab),
            "generated_at_ms": int(time.time() * 1000),
            "model": mdl,
            "from_pool": len(pool_mappings),
            "from_llm": len(clean),
        }
    except Exception as e:
        logger.exception(f"[phonetic_generator] failed for vocab='{vocab[:80]}…'")
        # Still return what the pool gave us — better than nothing.
        return {
            "mappings": pool_mappings,
            "vocab_hash": _vocab_hash(vocab),
            "generated_at_ms": int(time.time() * 1000),
            "from_pool": len(pool_mappings),
            "from_llm": 0,
            "error": str(e),
        }


def format_for_prompt(mappings: list[dict], limit: int = 60) -> str:
    """Format mappings list as bullet lines for injection into cleaner prompt.
    Returns empty string if no mappings (cleaner falls back to its built-in
    hardcoded examples in that case)."""
    if not mappings:
        return ""
    lines = []
    for m in mappings[:limit]:
        w = m.get("wrong", "").strip()
        c = m.get("correct", "").strip()
        if w and c:
            lines.append(f'  - "{w}" → "{c}"')
    return "\n".join(lines)
