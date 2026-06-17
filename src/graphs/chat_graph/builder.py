"""Assemble the chat StateGraph from the node factories + routers."""
from __future__ import annotations

from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from src.graphs._chat_state import ChatState
from src.graphs.chat_graph.agent import (
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_execute,
    route_after_agent_tools,
)
from src.graphs.chat_graph.classify import make_classify_intent, route_entry
from src.graphs.chat_graph.context import make_load_context, make_save_reply
from src.graphs.chat_graph.pm import (
    make_pm_call,
    pm_await,
    pm_error,
    pm_reply,
    route_after_pm_call,
    route_after_pm_error,
    route_after_pm_reply,
)

def build_chat_graph(
    session: AsyncSession, checkpointer, pm_client=None, agent_llm=None, *, tools=None
):
    g = StateGraph(ChatState)

    g.add_node("load_context", make_load_context(session))
    g.add_node("classify_intent", make_classify_intent(agent_llm))
    # Unified tool-calling agent (question + local tools). agent_llm + tools are
    # injected in tests; in production they default to _llm_client() / src.services.
    g.add_node("agent", make_agent(agent_llm, tools=tools))
    g.add_node("agent_tools", make_agent_tools(session, tools=tools))
    g.add_node("agent_approve", make_agent_approve(tools=tools))
    g.add_node("agent_execute", make_agent_execute(session, tools=tools))
    # pm-agent branch. pm_client is injected in tests; in production the
    # pm_call node lazily resolves get_pm_agent_client() on first use, so
    # non-PM chats never require PM_AGENT_* to be configured.
    g.add_node("pm_call", make_pm_call(pm_client))
    g.add_node("pm_await", pm_await)
    g.add_node("pm_reply", pm_reply)
    g.add_node("pm_error", pm_error)
    g.add_node("save_reply", make_save_reply(session))

    g.set_entry_point("load_context")
    g.add_edge("load_context", "classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        route_entry,
        {"agent": "agent", "pm_call": "pm_call"},
    )
    # unified agent loop: agent ⇄ agent_tools → (agent_approve → agent_execute) ↺
    g.add_conditional_edges(
        "agent",
        route_after_agent,
        {"agent_tools": "agent_tools", "save_reply": "save_reply"},
    )
    g.add_conditional_edges(
        "agent_tools",
        route_after_agent_tools,
        {"agent": "agent", "agent_approve": "agent_approve", "save_reply": "save_reply"},
    )
    g.add_edge("agent_approve", "agent_execute")
    g.add_conditional_edges(
        "agent_execute",
        route_after_agent_execute,
        {"agent": "agent", "save_reply": "save_reply"},
    )
    # pm-agent loop: pm_call → (await ⇄ pm_call) → pm_reply → save_reply
    g.add_conditional_edges(
        "pm_call",
        route_after_pm_call,
        {
            "pm_await": "pm_await",
            "pm_reply": "pm_reply",
            "pm_error": "pm_error",
            "save_reply": "save_reply",
        },
    )
    g.add_edge("pm_await", "pm_call")
    g.add_conditional_edges(
        "pm_error",
        route_after_pm_error,
        {"pm_call": "pm_call", "save_reply": "save_reply"},
    )
    # Chunked reconcile: pm_reply loops back to pm_call while groups remain.
    g.add_conditional_edges(
        "pm_reply",
        route_after_pm_reply,
        {"pm_call": "pm_call", "save_reply": "save_reply"},
    )
    g.add_edge("save_reply", END)

    return g.compile(checkpointer=checkpointer)
