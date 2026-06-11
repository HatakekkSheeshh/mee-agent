"""
Vocab Pool — persistent learned vocabulary for Whisper prompt.

Stores:
  - terms: list of unique tech/domain terms extracted from past MoMs
  - corrections: dict of {wrong_heard: correct_form} user-supplied fixes

Saved to output/vocab_pool.json, survives restarts.
"""
import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

POOL_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "vocab_pool.json")

_EXTRACT_PROMPT = """Bạn là trợ lý xử lý ngôn ngữ. Đọc biên bản họp JSON dưới đây và trích xuất các thuật ngữ kỹ thuật, tên sản phẩm, tên công nghệ, từ viết tắt đáng nhớ.

Yêu cầu:
- Chỉ lấy các từ/cụm từ kỹ thuật, tên riêng (tên sản phẩm, công nghệ, framework, tool) — không lấy từ thông dụng
- Giữ nguyên dạng viết của từ (không dịch, không lowercase toàn bộ)
- Tối đa 30 term
- Trả về CHỈ JSON array, ví dụ: ["VKS", "Helm chart", "pgvector", "RAG pipeline"]

Biên bản:
{mom_json}
"""


def load_pool() -> dict:
    os.makedirs(os.path.dirname(POOL_FILE), exist_ok=True)
    if not os.path.exists(POOL_FILE):
        return {"terms": [], "corrections": {}}
    try:
        with open(POOL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("terms", [])
            data.setdefault("corrections", {})
            return data
    except Exception:
        return {"terms": [], "corrections": {}}


def save_pool(pool: dict):
    os.makedirs(os.path.dirname(POOL_FILE), exist_ok=True)
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def add_correction(wrong: str, correct: str):
    """Save a user correction: Mee heard `wrong`, should be `correct`."""
    pool = load_pool()
    pool["corrections"][wrong.strip()] = correct.strip()
    # Also add the correct form to terms so it appears in future prompts
    correct_term = correct.strip()
    if correct_term and correct_term not in pool["terms"]:
        pool["terms"].append(correct_term)
    save_pool(pool)


def delete_correction(wrong: str):
    pool = load_pool()
    pool["corrections"].pop(wrong.strip(), None)
    save_pool(pool)


def bulk_add_corrections(mappings: list[dict]) -> int:
    """Batch-add phonetic mappings into the pool with a single save_pool().

    Each item: {"wrong": "<VN phonetic>", "correct": "<EN term>"}. Returns
    the count of NEW entries actually added (existing entries skipped).
    Use this for phonetic_generator output — saves N file writes vs N calls
    to add_correction.
    """
    if not mappings:
        return 0
    pool = load_pool()
    existing_terms_lower = {t.lower() for t in pool["terms"]}
    added = 0
    for m in mappings:
        if not isinstance(m, dict):
            continue
        w = str(m.get("wrong", "")).strip()
        c = str(m.get("correct", "")).strip()
        if not w or not c:
            continue
        if w in pool["corrections"]:
            continue  # already known
        pool["corrections"][w] = c
        added += 1
        if c.lower() not in existing_terms_lower:
            pool["terms"].append(c)
            existing_terms_lower.add(c.lower())
    if added:
        save_pool(pool)
    return added


def extract_corrections_from_edit(
    original_text: str,
    edited_text: str,
    max_pairs: int = 30,
) -> list[dict]:
    """Diff LLM-cleaned vs user-edited text → list of single-word corrections.

    Use case: every time a user saves a clean transcript edit (TipTap),
    extract their fixes as a learning signal. Single-word substitutions
    are the safe pattern — multi-word replacements (sentence rewrites)
    are too noisy to feed back to Whisper.

    Filters out:
      - Pure case changes (Whisper output is lowercase-ish anyway)
      - Punctuation-only changes
      - Multi-word replacements (too risky)
      - Short words < 3 chars (acronyms covered separately; common words
        skipped to avoid polluting pool)
      - Pairs where wrong == correct after normalisation

    Returns list of {"wrong": "...", "correct": "..."} compatible with
    bulk_add_corrections() — ready to persist into the pool.
    """
    import difflib
    import re

    if not original_text or not edited_text:
        return []
    if original_text.strip() == edited_text.strip():
        return []

    def _tokenize(t: str) -> list[str]:
        # Keep words (incl. underscore + dash), drop punctuation. Unicode
        # word class \w handles Vietnamese diacritics correctly.
        return re.findall(r"[\w-]+", t, re.UNICODE)

    a = _tokenize(original_text)
    b = _tokenize(edited_text)
    if not a or not b:
        return []

    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    seen: set[tuple[str, str]] = set()
    pairs: list[dict] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace":
            continue
        n_wrong = i2 - i1
        n_correct = j2 - j1
        # Allowed shapes:
        #   1→1  ("Vannot" → "pyannote")
        #   N→1  ("con vô lu sần" → "convolution") — common VN phonetic mishear
        #   1→N  (rare; usually a hyphenated splice fix)
        # Block N→M (multi-to-multi) — that's a paraphrase, too noisy.
        if n_wrong > 5 or n_correct > 5:
            continue
        if n_wrong > 1 and n_correct > 1:
            continue
        wrong = " ".join(a[i1:i2]).strip()
        correct = " ".join(b[j1:j2]).strip()
        if not wrong or not correct:
            continue
        if len(correct) < 3 or len(wrong) < 3:
            continue
        if len(wrong) > 60 or len(correct) > 60:
            continue  # avoid sentence-level edits
        if wrong.lower() == correct.lower():
            continue
        key = (wrong.lower(), correct.lower())
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"wrong": wrong, "correct": correct})
        if len(pairs) >= max_pairs:
            break
    return pairs


def get_corrections_for_vocab(vocab: str) -> list[dict]:
    """Return phonetic mappings from the pool whose `correct` value appears
    in the current meeting's vocab list. Lets the cleaner LLM see only
    relevant phonetic hints instead of the full pool (which may grow large).

    Format matches phonetic_generator output: [{"wrong":..., "correct":...}].
    """
    if not vocab or not vocab.strip():
        return []
    wanted = {t.strip().lower() for t in vocab.split(",") if t.strip()}
    pool = load_pool()
    out = []
    for wrong, correct in pool["corrections"].items():
        if correct.strip().lower() in wanted:
            out.append({"wrong": wrong, "correct": correct})
    return out


def terms_without_pool_corrections(vocab: str) -> list[str]:
    """Return the subset of vocab terms that the pool has NO phonetic mapping
    for yet. If empty → caller can skip the phonetic LLM call entirely
    because the pool already covers every term."""
    if not vocab or not vocab.strip():
        return []
    pool = load_pool()
    covered = {c.strip().lower() for c in pool["corrections"].values()}
    terms = [t.strip() for t in vocab.split(",") if t.strip()]
    return [t for t in terms if t.lower() not in covered]


def extract_and_save_terms(notes: dict, timeout: int = 60):
    """
    Call Claude to extract tech terms from a MoM dict, merge into vocab pool.
    Runs best-effort — failures are logged but don't break the flow.
    """
    try:
        mom_json = json.dumps(notes, ensure_ascii=False)
        prompt = _EXTRACT_PROMPT.format(mom_json=mom_json[:6000])  # cap to avoid huge prompts

        client = OpenAI(
            api_key=os.getenv("LLM_API_KEY", ""),
            base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
        )
        response = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            timeout=timeout,
        )
        output = response.choices[0].message.content.strip()
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()

        new_terms: list = json.loads(output)
        if not isinstance(new_terms, list):
            return

        pool = load_pool()
        existing_lower = {t.lower() for t in pool["terms"]}
        added = 0
        for term in new_terms:
            if isinstance(term, str) and term.strip() and term.strip().lower() not in existing_lower:
                pool["terms"].append(term.strip())
                existing_lower.add(term.strip().lower())
                added += 1

        save_pool(pool)
        logger.info(f"Vocab pool updated: +{added} new terms (total {len(pool['terms'])})")

    except Exception as e:
        logger.warning(f"Vocab extraction error (non-critical): {e}")


def build_pool_prompt_fragment() -> str:
    """
    Return a string to append to initial_prompt with learned terms + corrections.
    Empty string if pool is empty.
    """
    pool = load_pool()
    parts = []

    if pool["terms"]:
        terms_str = ", ".join(pool["terms"][:80])  # cap to keep prompt size sane
        parts.append(f"Các thuật ngữ đã học từ cuộc họp trước: {terms_str}.")

    if pool["corrections"]:
        pairs = "; ".join(f'"{w}" → "{c}"' for w, c in list(pool["corrections"].items())[:20])
        parts.append(f"Lưu ý sửa lỗi nhận dạng: {pairs}.")

    return " ".join(parts)
