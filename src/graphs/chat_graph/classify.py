"""Intent classification node + entry router."""
from __future__ import annotations

import json
import logging
import re
from typing import Literal

from src.graphs._chat_llm import _llm_client, _llm_model
from src.graphs._chat_serde import strip_think
from src.graphs._chat_prompts import CLASSIFY_SYSTEM_PROMPT
from src.graphs._chat_state import ChatState, PM_AGENT_COMMAND

logger = logging.getLogger(__name__)


def _pm_agent_opt_in(msg: str) -> tuple[bool, str]:
    """A message starting with /pm-agent (case-insensitive, leading ws ok) is an
    explicit opt-in to the pm-agent branch. Returns (opted_in, message_without_command)."""
    stripped = (msg or "").lstrip()
    if stripped[: len(PM_AGENT_COMMAND)].lower() == PM_AGENT_COMMAND:
        return True, stripped[len(PM_AGENT_COMMAND):].lstrip()
    return False, msg


def make_classify_intent(llm=None):
    async def classify_intent(state: ChatState) -> dict:
        """Router: '/pm-agent' prefix → pm_task (explicit opt-in, no LLM call);
        otherwise intent is ALWAYS 'agent' and the LLM only decides the grounding
        flag (whether the agent must read real meeting data first).

        The unified tool-calling agent handles all meeting Q&A + local tools
        (incl. Redmine via MCP), so the pm-agent A2A branch is STRICTLY opt-in —
        the LLM classifier can never escalate to pm_task on its own."""
        msg = state["user_message"]
        opted_in, cleaned = _pm_agent_opt_in(msg)
        if opted_in:
            logger.info("[Node classify_intent] /pm-agent → pm_task (explicit opt-in)")
            # Strip the command so pm_call forwards the real request, not the prefix.
            return {"intent": "pm_task", "grounding": "auto", "user_message": cleaned}
        try:
            client = llm or _llm_client()
            resp = client.chat.completions.create(
                model=_llm_model(),
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Tin nhắn user: {msg}"},
                ],
                max_tokens=64,
                timeout=60,
                # Reasoning models (e.g. minimax-m2.5) otherwise burn max_tokens on
                # <think> and return empty content — disable it for this tiny task.
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            # content can be None (empty/refused/all-reasoning) — guard, then strip
            # any leaked <think> reasoning (minimax ignores enable_thinking at times).
            raw = strip_think(resp.choices[0].message.content)
            if not raw:
                logger.warning(
                    "[Node classify_intent] empty content from model — defaulting to agent/auto"
                )
                return {"intent": "agent", "grounding": "auto"}
            # Strip code fences if any
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            # Tolerate prose around the JSON object; a parse miss is a known,
            # recovered condition (not a crash) → default quietly, no ERROR traceback.
            if not raw.startswith("{"):
                m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
                raw = m.group(0) if m else raw
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "[Node classify_intent] non-JSON content %r — defaulting to agent/auto",
                    raw[:120],
                )
                return {"intent": "agent", "grounding": "auto"}
            # Opt-in only: intent is ALWAYS 'agent' off the /pm-agent prefix. Any
            # intent field the model emits is deliberately ignored — the LLM is a
            # grounding-only classifier now.
            # grounding="required" forces a tool call on the agent's first turn
            # (content/recording questions); default "auto" when absent/invalid so
            # a model that omits the field never accidentally forces grounding.
            grounding = parsed.get("grounding")
            if grounding not in ("required", "auto"):
                grounding = "auto"
            logger.info(f"[Node classify_intent] intent='agent' grounding={grounding!r}")
            return {"intent": "agent", "grounding": grounding}
        except Exception as e:
            logger.exception("classify_intent failed")
            return {"intent": "agent", "grounding": "auto", "error": f"classify failed: {e}"}

    return classify_intent

def route_entry(state: ChatState) -> Literal["pm_call", "agent"]:
    """Conditional edge after classify: pm-agent branch, or the unified agent."""
    return "pm_call" if state.get("intent") == "pm_task" else "agent"
