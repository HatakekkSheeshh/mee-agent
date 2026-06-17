"""Pure LLM resolution of a user-named project/meeting → an existing meeting id.

ILIKE substring search (``repo.find_meetings_by_title``) only matches when the
query is a substring of the title, so it fails on two common cases:
  - the extracted phrase is longer than the title — title "GIP" is NOT inside the
    query "GIP có gì", so substring search returns 0 hits;
  - duplicate / near-duplicate titles ("AI Innovation Project" vs "AI Innovation
    Projects") — substring search returns >1 hit with no way to pick.
When ILIKE is ambiguous (0 or >1 hits), we hand the user's phrase + the candidate
titles to the LLM and let it pick the best-matching id — or answer NONE.

The chosen id is validated against the candidate set: the model can only pick an
EXISTING meeting, never invent one. ``generate(messages) -> str`` is injected so
this module stays pure and unit-testable (no network). Mirrors the shape of
``role_mapping.classify_role``.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)
_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)

_SYSTEM = """\
Bạn giúp xác định người dùng đang nói tới DỰ ÁN/CUỘC HỌP nào trong một danh sách
CỐ ĐỊNH. Người dùng có thể gọi tên kèm thêm chữ thừa ("meeting GIP có gì" → tên
"GIP"), viết tắt, đổi cách viết, hay tên gần giống nhau ("AI Innovation Project"
vs "AI Innovation Projects"). Hãy chọn dự án khớp nhất.

CHỈ được chọn một id CÓ SẴN trong danh sách — KHÔNG bịa id mới. Nếu không dự án
nào hợp lý, trả về đúng chữ: NONE.

Trả về DUY NHẤT một JSON: {"meeting_id": "<id chính xác trong danh sách>"}.
"""


def _strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


def default_generate(messages: list[dict[str, str]]) -> str:
    """Blocking LLM call wiring `llm_resolve_meeting` to the chat LLM.

    The default `generate` for production callers; tests inject a fake instead.
    Reuses the chat graph's `_llm_client`/`_llm_model` (lazy import keeps this
    module's import side-effect-free and avoids a graphs↔services import cycle).
    """
    from src.graphs._chat_llm import _llm_client, _llm_model

    resp = _llm_client().chat.completions.create(
        model=_llm_model(),
        messages=messages,
        max_tokens=128,
        timeout=60,
        # Reasoning models otherwise burn the budget on <think> — disable it.
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return resp.choices[0].message.content or ""


def find_meeting_named_in(
    message: str,
    candidates: Sequence,
    *,
    current_meeting_id: str | None = None,
) -> str | None:
    """Deterministically resolve a meeting the user NAMED in `message` — no LLM.

    Returns the id of a candidate whose title appears as a whole token in the
    message (case-insensitive); the LONGEST matching title wins, and on a tie a
    meeting other than ``current_meeting_id`` is preferred. This takes the
    decision away from the model: when the user types an exact meeting name
    ("GIP"), the agent can't merge it into the current meeting. Returns None when
    no title is named.
    """
    msg = (message or "").strip().lower()
    if not msg or not candidates:
        return None
    matches: list[tuple[int, bool, str]] = []  # (title_len, is_current, id)
    for c in candidates:
        title = (getattr(c, "title", "") or "").strip()
        if not title:
            continue
        # Whole-token match: \w boundaries so "Test" ≠ "Testing"/"latest".
        pattern = r"(?<!\w)" + re.escape(title.lower()) + r"(?!\w)"
        if re.search(pattern, msg):
            cid = str(c.id)
            matches.append((len(title), cid == str(current_meeting_id or ""), cid))
    if not matches:
        return None
    matches.sort(key=lambda m: (-m[0], m[1]))  # longest title first; non-current on ties
    return matches[0][2]


def build_meeting_match_messages(query: str, candidates: Sequence) -> list[dict[str, str]]:
    """Build the chat messages for the meeting-match LLM call (pure).

    ``candidates`` is any iterable of objects with ``.id`` and ``.title``. Each
    candidate is listed as ``- <id> — <title>`` so the model returns an id verbatim.
    """
    catalog = "\n".join(
        f"- {c.id} — {(c.title or '').strip()}" for c in candidates
    )
    user = (
        f'Người dùng nhắc tới dự án: "{(query or "").strip()}"\n\n'
        f"Danh sách dự án:\n{catalog}"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def llm_resolve_meeting(
    query: str,
    candidates: Sequence,
    *,
    generate: Callable[[list[dict[str, str]]], str],
) -> str | None:
    """Resolve ``query`` to a candidate meeting id via the LLM, or None.

    Returns the matched id as a string only when the model picks an id that is
    actually in ``candidates`` (never invents). Blank query, empty candidates, a
    NONE answer, an out-of-set id, or a generate() failure all yield None.
    """
    if not query or not query.strip() or not candidates:
        return None
    candidates = list(candidates)
    valid_ids = {str(c.id) for c in candidates}
    messages = build_meeting_match_messages(query, candidates)
    try:
        raw = _strip_think(generate(messages))
    except Exception:
        logger.warning("llm_resolve_meeting: generate() failed for %r", query, exc_info=True)
        return None
    logger.debug("llm_resolve_meeting(%r): raw=%r", query, raw)
    if not raw or raw.strip().upper() == "NONE":
        return None
    m = _JSON_RE.search(raw)
    if not m:
        logger.debug("llm_resolve_meeting(%r): no JSON object in answer", query)
        return None
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        logger.debug("llm_resolve_meeting(%r): JSON parse failed for %r", query, m.group(0))
        return None
    chosen = obj.get("meeting_id")
    chosen = str(chosen) if chosen is not None else None
    if chosen not in valid_ids:
        logger.debug("llm_resolve_meeting(%r): %r not in candidates", query, chosen)
        return None
    logger.debug("llm_resolve_meeting(%r): -> %r", query, chosen)
    return chosen
