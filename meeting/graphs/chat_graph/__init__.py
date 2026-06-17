"""chat_graph package facade.

Re-exports every public name so `from meeting.graphs.chat_graph import X` and
`chat_graph.X` keep resolving after the Phase 2 split. `repo` is re-exported so
tests patching `chat_graph.repo.*` (a module attribute = shared state) still reach
the submodules, which import the same repositories module.
"""
from meeting.db import repositories as repo
from meeting.graphs._chat_llm import _llm_client, _llm_model
from meeting.graphs._chat_prompts import (
    CLASSIFY_SYSTEM_PROMPT,
    _agent_system_prompt,
    _to_llm_messages,
)
from meeting.graphs._chat_serde import (
    MAX_RECONCILE_ITEMS,
    _decision_to_payload,
    _json,
    _last_assistant_text,
    _parse_tool_args,
    _reconcile_payloads,
    _reconcile_text,
    _result_to_dict,
    _seed_agent_messages,
    _tc_to_dict,
)
from meeting.graphs._chat_state import ChatState, MAX_AGENT_ROUNDS, PM_MAX_ROUNDS
from meeting.graphs.chat_graph.agent import (
    REJECT_REPLY,
    _build_reconcile_template,
    _inject_meeting,
    _openai_tools,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_execute,
    route_after_agent_tools,
)
from meeting.graphs.chat_graph.builder import build_chat_graph
from meeting.graphs.chat_graph.classify import make_classify_intent, route_entry
from meeting.graphs.chat_graph.context import (
    make_load_context,
    make_save_reply,
    resolve_meeting,
)
from meeting.graphs.chat_graph.pm import (
    make_pm_call,
    pm_await,
    pm_error,
    pm_reply,
    route_after_pm_call,
    route_after_pm_error,
    route_after_pm_reply,
)
from meeting.graphs.chat_graph.runner import (
    _initial_turn_state,
    _interrupt_or_complete,
    resume_chat_turn,
    run_chat_turn,
    stream_chat_turn,
    update_to_events,
)
