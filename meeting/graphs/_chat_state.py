"""Chat graph state schema + loop-safety constants.

Extracted from chat_graph.py (pure, no test-patched seams). Re-imported there so
every `chat_graph.X` reference still resolves.
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict

# Safety cap on the pm_call ⇄ pm_await loop against a misbehaving agent.
PM_MAX_ROUNDS = 6

# Safety cap on the unified agent ⇄ tools loop (number of LLM tool-calling rounds).
MAX_AGENT_ROUNDS = 6

# Explicit opt-in to the pm-agent A2A branch. A message starting with this
# command (case-insensitive, leading whitespace ok) routes deterministically to
# pm_task; everything else stays in the unified agent, which now owns the
# Redmine MCP tools. The FE highlights this token as a command chip.
PM_AGENT_COMMAND = "/pm-agent"


# ─── State ────────────────────────────────────────────────────────

class ChatState(TypedDict, total=False):
    # Input
    session_id: str            # ChatSession.id (also LangGraph thread_id)
    user_id: str
    user_message: str          # latest user message text
    meeting_id: Optional[str]  # if chat is bound to a meeting

    # Loaded by load_context
    meeting_context: dict      # title, project_summary_json, recording_moms[]
    project_memory: str        # distilled current-state recalled from AgentBase (best-effort)
    recent_messages: list[dict]  # last N messages from chat_messages
    resolved_meeting_id: Optional[str]  # bound meeting (or title-resolved) for tool scoping

    # Filled by classify_intent (binary router: agent vs pm_task)
    intent: Literal["agent", "pm_task"]
    # Force-grounding signal (also from classify): "required" makes the agent's
    # first turn use tool_choice="required" so it must read real data before
    # answering content/recording questions; "auto" = normal. Defaults to "auto".
    grounding: Literal["required", "auto"]

    # Filled after interrupt + resume
    user_decision: Optional[dict]  # {action: 'approved'|'rejected', edited_args?, reason?}

    # Filled by the agent loop / pm branch
    tool_result: Optional[dict]
    final_reply: str           # text to show user

    # ── unified tool-calling agent (intent == "agent") ──
    # All checkpointed (thread_id = session_id) so the tool loop survives an
    # approve/reject round-trip. agent_messages is the running OpenAI message
    # list (assistant tool_calls + tool results); pending_tool is the one
    # side-effect call awaiting HITL approval.
    agent_messages: list[dict]
    agent_rounds: int
    pending_tool: Optional[dict]   # {id, name, args} of the side-effect call to approve
    agent_route: Optional[str]     # "tools" | "finish" | "approve" | "agent"

    # ── pm-agent A2A branch (intent == "pm_task") ──
    # All checkpointed (thread_id = session_id) so a multi-step pm-agent
    # conversation survives across approve/reject round-trips on one thread.
    pm_task_id: Optional[str]      # A2A task id; None on first call, set from result
    pm_context_id: Optional[str]   # A2A contextId; echoed with task_id on resume
    pm_next_payload: dict          # what pm_call sends next:
    #   {kind:"start"|"text", text} | {kind:"approval", approval_action, approval_input}
    pm_last: Optional[dict]        # last PmAgentResult, as a dict
    pm_pending: Optional[dict]     # payload handed to interrupt() for the FE
    pm_rounds: int                 # loop counter for PM_MAX_ROUNDS
    pm_route: Optional[str]        # pm_call → router hint: "await"|"reply"|"end"|"error"|"retry"
    pm_last_error: Optional[str]   # last pm transport error (shown on the retry card)
    pm_queue: list                 # chunked-reconcile payloads still to send (one per assignee group)
    pm_replies: list               # accumulated group replies, joined into final_reply when queue drains

    # Internal
    error: Optional[str]
