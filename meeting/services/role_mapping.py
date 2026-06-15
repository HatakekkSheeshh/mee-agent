"""Pure jobTitle → roles.name resolution.

Free-text Entra `jobTitle` strings vary by word order ("Applied AI" ↔ "AI
Applied") and carry extra/seniority words. We do NOT strip seniority
algorithmically — several pool names deliberately contain "Lead …"/"Associate
…", so stripping would corrupt them. Variance is handled by explicit per-role
`aliases` (seed data). `normalize` only absorbs case/punctuation/whitespace
noise. Unknown title → None (never guess: a wrong role pulls the wrong
data_plan). See docs/superpowers/specs/2026-06-14-oid-role-persona-design.md.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(title: str | None) -> str:
    """Lowercase, collapse any run of non-alphanumerics to a single space."""
    return _NON_ALNUM.sub(" ", (title or "").lower()).strip()


def resolve_role(job_title: str | None, roles) -> str | None:
    """Return the matching role's name, or None.

    `roles` is any iterable of objects with `.name: str` and `.aliases:
    list[str]`. Matches normalized `job_title` against each role's normalized
    name (implicit alias) + its normalized aliases.
    """
    target = normalize(job_title)
    if not target:
        return None
    for role in roles:
        candidates = [role.name, *(getattr(role, "aliases", None) or [])]
        if any(normalize(c) == target for c in candidates):
            return role.name
    return None


_CLASSIFY_SYSTEM = """\
Bạn phân loại CHỨC DANH (jobTitle) của một nhân sự vào ĐÚNG MỘT vai trò trong
danh sách CỐ ĐỊNH dưới đây. CHỈ được chọn tên vai trò có sẵn — KHÔNG bịa tên mới.

Chức danh đầu vào có thể chứa từ chỉ CẤP BẬC (Intern/Junior/Senior/Lead/Staff/
Principal/Trưởng/Phó…). Cấp bậc KHÔNG phải là vai trò — hãy phân loại theo CHỨC
NĂNG/LĨNH VỰC cốt lõi và chọn vai trò gần nhất NGAY CẢ KHI cấp bậc khác nhau.

Các vai trò (name — mô tả — data_plan):
{catalog}

Chức danh cần phân loại: "{job_title}"

Trả về DUY NHẤT một JSON: {{"role": "<tên vai trò chính xác trong danh sách>",
"confidence": <0..1>}}. Nếu không vai trò nào hợp lý, trả về đúng chữ: NONE
"""


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def classify_role(
    job_title: str | None,
    roles: Sequence,                      # objects with .name/.description/.data_plan
    *,
    generate: Callable[[list[dict[str, str]]], str],
    threshold: float = 0.6,
) -> str | None:
    """LLM fallback: map an unmatched jobTitle to the best-fit EXISTING role name.

    `generate(messages) -> str` is injected. Returns a pool role name only when the
    model is confident AND the name is in the pool; otherwise None (never invents).
    """
    if not job_title or not roles:
        return None
    roles = list(roles)
    pool = {r.name for r in roles}
    catalog = "\n".join(
        f"- {r.name} — {(r.description or '').strip()} — {r.data_plan}" for r in roles
    )
    content = _CLASSIFY_SYSTEM.format(catalog=catalog, job_title=job_title)
    try:
        raw = _strip_think(generate([{"role": "system", "content": content}]))
    except Exception:
        logger.warning("classify_role: generate() failed for %r", job_title, exc_info=True)
        return None
    logger.debug("classify_role(%r): raw=%r", job_title, raw)
    if not raw or raw.strip().upper() == "NONE":
        logger.debug("classify_role(%r): model answered NONE/empty", job_title)
        return None
    m = _JSON_RE.search(raw)
    if not m:
        logger.debug("classify_role(%r): no JSON object in answer", job_title)
        return None
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        logger.debug("classify_role(%r): JSON parse failed for %r", job_title, m.group(0))
        return None
    name = obj.get("role")
    conf = obj.get("confidence")
    if name not in pool:
        logger.debug("classify_role(%r): %r not in pool (conf=%r)", job_title, name, conf)
        return None
    try:
        if float(conf) < threshold:
            logger.debug("classify_role(%r): %r conf=%s < threshold %s", job_title, name, conf, threshold)
            return None
    except (TypeError, ValueError):
        logger.debug("classify_role(%r): non-numeric confidence %r", job_title, conf)
        return None
    logger.debug("classify_role(%r): -> %r (conf=%s)", job_title, name, conf)
    return name
