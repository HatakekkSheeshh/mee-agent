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


def canonical_source_hash(project_summary: dict | None, moms: list[dict | None]) -> str:
    """Stable sha256 hex of the distillation inputs.

    Key-order independent (sort_keys), None-safe. Two equal logical inputs hash
    equal; any content change flips the hash → drives change detection.
    """
    payload = {"summary": project_summary, "moms": list(moms)}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
