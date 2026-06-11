"""Agent-memory sync — distill Postgres project data into a condensed
current-state text for the AgentBase Memory projection.

Two network-free helpers (the bulk of the logic, unit-tested without DB/network):
  - canonical_source_hash: deterministic content hash of the distillation
    inputs, used for change detection (skip re-distilling unchanged projects);
  - distill_project_state: assemble the LLM prompt from project_summary_json +
    recordings' mom_json and return the condensed state text. The OpenAI-compatible
    client is INJECTED (DI seam) so tests run without network.

The standalone runner (scripts/sync_memory.py) and the AgentBase upsert live
elsewhere — this module stays pure/injectable.

Spec: docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Reuse the Qwen3/Gemma <think> stripping idiom from note_generator.
_THINK_TAG_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", flags=re.DOTALL | re.IGNORECASE)

DISTILL_PROMPT = """Bạn là trợ lý ghi nhớ trạng thái project. Dưới đây là tổng kết \
project và biên bản các phiên họp. Hãy chắt lọc thành một bản trạng thái HIỆN TẠI \
thật ngắn gọn (tiếng Việt), gồm: giai đoạn/tiến độ, các quyết định còn hiệu lực, \
blocker đang mở, ai phụ trách việc gì. Chỉ nêu sự thật từ dữ liệu, không suy diễn.

Dữ liệu nguồn (JSON):
{source}

Trả về văn bản trạng thái thuần (không JSON, không markdown thừa)."""


# Bump when the PROJECTION FORMAT changes (not the source data) — e.g. adding the
# per-session bullets. Folded into the hash so every record re-syncs once on the
# next run even though summary/moms are unchanged. History:
#   1 = aggregate-only;  2 = aggregate + "Theo từng phiên" per-session bullets.
PROJECTION_VERSION = 2


def canonical_source_hash(project_summary: dict | None, moms: list[dict | None]) -> str:
    """Stable sha256 hex of the distillation inputs + projection version.

    Key-order independent (sort_keys), None-safe. Two equal logical inputs hash
    equal; any source change OR a PROJECTION_VERSION bump flips the hash → drives
    change detection (so a format change invalidates stale records automatically).
    """
    payload = {"v": PROJECTION_VERSION, "summary": project_summary, "moms": list(moms)}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mom_texts(items) -> list[str]:
    """Extract strings from a decisions/blockers/commitments list (str or {text,by})."""
    out: list[str] = []
    for it in items or []:
        if isinstance(it, str) and it.strip():
            out.append(it.strip())
        elif isinstance(it, dict) and it.get("text"):
            txt = it["text"].strip()
            by = it.get("by")
            out.append(f"{txt} (bởi {by})" if by else txt)
    return out


def build_session_bullets(sessions: list[dict]) -> str:
    """Deterministic per-session bullets from each recording's MoM (no LLM).

    `sessions`: ordered [{"label", "date", "mom"}]. Produces a "Theo từng phiên"
    block so the project record answers per-recording questions without Postgres.
    Returns "" when no session has any decisions/action-items/blockers.
    """
    blocks: list[str] = []
    for s in sessions:
        mom = s.get("mom") or {}
        decisions = _mom_texts(mom.get("decisions"))
        blockers = _mom_texts(mom.get("blockers"))
        action_items = [ai for ai in (mom.get("action_items") or []) if isinstance(ai, dict) and ai.get("item")]
        if not (decisions or blockers or action_items):
            continue
        date = (s.get("date") or "")[:10]
        header = f"### {s.get('label') or 'Phiên họp'}" + (f" ({date})" if date else "")
        lines = [header]
        if decisions:
            lines.append("- Quyết định: " + "; ".join(decisions))
        for ai in action_items:
            seg = f"- Việc: {ai['item']}"
            if ai.get("pic"):
                seg += f" — {ai['pic']}"
            if ai.get("deadline"):
                seg += f" (hạn {ai['deadline']})"
            lines.append(seg)
        if blockers:
            lines.append("- Blocker: " + "; ".join(blockers))
        blocks.append("\n".join(lines))
    return "Theo từng phiên:\n\n" + "\n\n".join(blocks) if blocks else ""


def _strip_think(text: str) -> str:
    text = _THINK_TAG_RE.sub("", text)
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


def distill_project_state(
    project_summary: dict | None,
    moms: list[dict | None],
    *,
    client: Any,
    model: str,
) -> str:
    """Call the injected LLM to condense project data into current-state text."""
    source = json.dumps(
        {"summary": project_summary, "moms": list(moms)},
        ensure_ascii=False,
        default=str,
    )
    prompt = DISTILL_PROMPT.format(source=source)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = resp.choices[0].message.content or ""
    return _strip_think(content)


# ── Sync orchestration (network-free, injectable seams) ─────────────────────

def plan_project_sync(
    project_summary: dict | None,
    moms: list[dict | None],
    existing_hash: str | None,
) -> tuple[str, str]:
    """Decide the action for one project. Returns (action, source_hash).

    action ∈ {"empty", "skip", "sync"}:
      - "empty": no summary and no MoM content → nothing to distill (hash "").
      - "skip" : current hash equals the stored one → unchanged, no LLM/write.
      - "sync" : content changed (or never synced) → distill + upsert.
    """
    has_content = bool(project_summary) or any(m for m in moms)
    if not has_content:
        return "empty", ""
    source_hash = canonical_source_hash(project_summary, moms)
    if existing_hash == source_hash:
        return "skip", source_hash
    return "sync", source_hash


def sync_one_project(
    *,
    project_id: str,
    project_summary: dict | None,
    moms: list[dict | None],
    get_existing_hash,
    distill,
    upsert_record,
    dry_run: bool = False,
) -> dict:
    """Run change-detection + (conditional) distill/upsert for one project.

    All side-effecting deps are injected callables so this is fully unit-testable
    without DB, network, or an LLM:
      - get_existing_hash(project_id) -> str | None   (reads AgentBase marker hash)
      - distill(project_summary, moms) -> str          (LLM)
      - upsert_record(project_id, text, source_hash)   (AgentBase insert)

    Returns {"action", "hash", "text"?}. On "empty"/"skip" neither distill nor
    upsert is called. On "sync" with dry_run=True, distill runs but upsert does not.
    """
    existing_hash = get_existing_hash(project_id)
    action, source_hash = plan_project_sync(project_summary, moms, existing_hash)
    if action != "sync":
        return {"action": action, "hash": source_hash}

    text = distill(project_summary, moms)
    if not dry_run:
        upsert_record(project_id, text, source_hash)
    return {"action": action, "hash": source_hash, "text": text}
