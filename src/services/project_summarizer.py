"""Project Summary service — aggregate decisions across recordings into a timeline.

Scope (hackathon, focused):
    - decisions_timeline: chronological list of decisions per recording
    - narrative: 1 LLM-generated paragraph describing project trajectory

Future:
    - consolidated_action_items (dedupe across sessions)
    - recurring_blockers
    - progress narrative end-to-end
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Optional

from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import repositories as repo
from src.services.memory_sync_runner import schedule_project_sync

logger = logging.getLogger(__name__)

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*$", flags=re.DOTALL | re.IGNORECASE)

SUMMARY_PROMPT = """Bạn là trợ lý tổng kết project. Dưới đây là timeline các decisions được đưa ra qua nhiều cuộc họp trong cùng 1 project. Hãy viết 1 đoạn văn ngắn (3-5 câu) mô tả tiến trình project: từ decisions ban đầu → decisions hiện tại, xu hướng quyết định, sự thay đổi/nhất quán trong định hướng.

Project: {project_title}
Số phiên họp: {session_count}

Timeline decisions:
{timeline_text}

CHỈ trả về JSON với format sau, KHÔNG markdown, KHÔNG giải thích:

{{
  "narrative": "<đoạn văn 3-5 câu tóm tắt tiến trình quyết định của project>"
}}"""


def _get_llm_client():
    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )
    model = os.getenv("LLM_MODEL", "Qwen/Qwen3-8B")
    return client, model


def _call_llm_json(prompt: str, max_tokens: int = 800, timeout: int = 60) -> dict:
    client, model = _get_llm_client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        output = (response.choices[0].message.content or "").strip()
        output = _THINK_TAG_RE.sub("", output)
        output = _THINK_OPEN_RE.sub("", output).strip()
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0].strip()
        elif "```" in output:
            output = output.split("```")[1].split("```")[0].strip()
        return json.loads(output)
    except Exception as e:
        logger.error(f"Project summary LLM failed: {e}")
        return {"error": f"LLM call failed: {e}"}


def _extract_decisions(mom_json: dict) -> list[str]:
    """Pull decision strings out of a recording's mom_json (handles str or dict shape)."""
    out: list[str] = []
    for dec in (mom_json or {}).get("decisions", []) or []:
        if isinstance(dec, str) and dec.strip():
            out.append(dec.strip())
        elif isinstance(dec, dict) and dec.get("text"):
            text = dec["text"].strip()
            by = dec.get("by")
            out.append(f"{text} (bởi {by})" if by else text)
    return out


async def generate_project_summary(
    session: AsyncSession, meeting_id: uuid.UUID
) -> dict:
    """Aggregate per-recording MoMs into a project-level summary.

    Returns the summary dict (also persisted via repo.save_project_summary).
    """
    meeting = await repo.get_meeting(session, meeting_id)
    if not meeting:
        return {"error": "Meeting not found"}

    recordings = meeting.recordings or []
    # Chronological: oldest → newest
    recordings_sorted = sorted(
        [r for r in recordings if r.started_at],
        key=lambda r: r.started_at,
    )

    timeline: list[dict] = []
    for r in recordings_sorted:
        if not r.mom_json:
            continue
        decisions = _extract_decisions(r.mom_json)
        if not decisions:
            continue
        timeline.append({
            "recording_id": str(r.id),
            "session_label": r.session_label or "Phiên họp",
            "date": r.started_at.isoformat() if r.started_at else None,
            "decisions": decisions,
        })

    if not timeline:
        summary = {
            "project_title": meeting.title,
            "session_count": len(recordings_sorted),
            "decisions_timeline": [],
            "narrative": "Chưa có decisions nào được ghi nhận. Tạo biên bản cho các phiên họp trước khi tổng kết.",
            "generated_at": datetime.utcnow().isoformat(),
        }
        await repo.save_project_summary(session, meeting_id, summary)
        schedule_project_sync(meeting_id)
        return summary

    # Build text blob for LLM narrative
    timeline_lines = []
    for entry in timeline:
        dt = entry["date"][:10] if entry["date"] else "—"
        timeline_lines.append(f"\n[{dt}] {entry['session_label']}:")
        for dec in entry["decisions"]:
            timeline_lines.append(f"  • {dec}")
    timeline_text = "\n".join(timeline_lines)

    prompt = SUMMARY_PROMPT.format(
        project_title=meeting.title or "(Untitled project)",
        session_count=len(timeline),
        timeline_text=timeline_text,
    )

    # Sync OpenAI SDK call → wrap in thread to keep FastAPI event loop free
    # (otherwise project-summary generation blocks every other request for
    # the duration of the LLM run — sidebar fetches pile up pending).
    import asyncio as _aio
    llm_out = await _aio.to_thread(_call_llm_json, prompt, 600)
    narrative = llm_out.get("narrative", "") if "error" not in llm_out else ""
    if "error" in llm_out:
        logger.warning(f"Narrative LLM failed: {llm_out['error']}. Returning timeline only.")

    summary = {
        "project_title": meeting.title,
        "session_count": len(timeline),
        "decisions_timeline": timeline,
        "narrative": narrative,
        "generated_at": datetime.utcnow().isoformat(),
    }
    await repo.save_project_summary(session, meeting_id, summary)
    schedule_project_sync(meeting_id)
    return summary
