"""Intent classification node + entry router."""
from __future__ import annotations

import json
import logging
from typing import Literal

from meeting.graphs._chat_llm import _llm_client, _llm_model
from meeting.graphs._chat_prompts import CLASSIFY_SYSTEM_PROMPT
from meeting.graphs._chat_state import ChatState

logger = logging.getLogger(__name__)

def make_classify_intent(llm=None):
    async def classify_intent(state: ChatState) -> dict:
        """Binary router: 'pm_task' (Redmine via pm-agent) vs 'agent' (everything else).

        The unified tool-calling agent handles all meeting Q&A + local tools, so the
        only split left is whether to hand off to the separate pm-agent A2A branch.
        """
        msg = state["user_message"]
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
            )
            raw = resp.choices[0].message.content.strip()
            # Strip code fences if any
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            parsed = json.loads(raw)
            intent = parsed.get("intent")
            if intent not in ("pm_task", "agent"):
                intent = "agent"
            # grounding="required" forces a tool call on the agent's first turn
            # (content/recording questions); default "auto" when absent/invalid so
            # a model that omits the field never accidentally forces grounding.
            grounding = parsed.get("grounding")
            if grounding not in ("required", "auto"):
                grounding = "auto"
            logger.info(
                f"[Node classify_intent] intent={intent!r} grounding={grounding!r}"
            )
            return {"intent": intent, "grounding": grounding}
        except Exception as e:
            logger.exception("classify_intent failed")
            return {"intent": "agent", "grounding": "auto", "error": f"classify failed: {e}"}

    return classify_intent

def route_entry(state: ChatState) -> Literal["pm_call", "agent"]:
    """Conditional edge after classify: pm-agent branch, or the unified agent."""
    return "pm_call" if state.get("intent") == "pm_task" else "agent"
