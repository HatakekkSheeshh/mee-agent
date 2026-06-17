"""Tool registry + the local `@tool` decorator.

Each tool lives in its own module (send_email.py, create_task.py, …) and
self-registers by decorating its async executor with `@tool(...)`. The package
`__init__` imports every tool module so the decorators fire at load time —
there is no manual TOOLS dict to edit when adding a tool.

A registered spec is the same shape the rest of the app already consumes:
    {name, description, side_effect, schema (raw JSON Schema), executor}

We deliberately do NOT use langchain_core's `@tool`: that builds a
StructuredTool for LangChain/LangGraph's own agent executors, which this repo
doesn't use. Our agent loop speaks native OpenAI tool-calling (chat_graph
`_openai_tools` consumes `schema` directly) and needs `side_effect` (HITL
gating) plus per-call `session`/`user_id` injection — none of which the
StructuredTool contract carries. The local decorator keeps that contract intact.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from meeting.db import repositories as repo

logger = logging.getLogger(__name__)

# name → spec ({name, description, side_effect, schema, executor}).
# Insertion order = registration order = the order tools are offered to the LLM.
TOOLS: dict[str, dict[str, Any]] = {}

ToolExecutor = Callable[..., Awaitable[dict]]


def tool(
    *,
    name: str,
    description: str,
    side_effect: bool = False,
    schema: Optional[dict] = None,
) -> Callable[[ToolExecutor], ToolExecutor]:
    """Register an async executor as a tool. Returns the function unchanged so
    the module can also export plain helpers alongside it.

    The executor signature is `async (args: dict, *, session, user_id) -> dict`.
    """

    def decorator(fn: ToolExecutor) -> ToolExecutor:
        if name in TOOLS:
            logger.warning("[tools] re-registering tool %r (overwrites prior spec)", name)
        TOOLS[name] = {
            "name": name,
            "description": description,
            "side_effect": side_effect,
            "schema": schema or {"type": "object", "properties": {}},
            "executor": fn,
        }
        return fn

    return decorator


def list_tools() -> list[dict]:
    """Return tool specs for the LLM prompt (without executor)."""
    return [
        {k: v for k, v in spec.items() if k != "executor"}
        for spec in TOOLS.values()
    ]


def get_tool(name: str) -> Optional[dict]:
    return TOOLS.get(name)


async def execute_tool(
    name: str,
    args: dict,
    *,
    session,
    user_id,
) -> dict:
    """Run tool by name. Audit-logged."""
    spec = TOOLS.get(name)
    if not spec:
        raise ValueError(f"Unknown tool: {name}")
    executor = spec["executor"]
    try:
        result = await executor(args, session=session, user_id=user_id)
        await repo.log_audit(
            session,
            user_id=user_id,
            session_id=None,  # caller passes session_id via wrapper
            action_type="tool_execute",
            tool_name=name,
            tool_args=args,
            result=result,
            success=True,
        )
        return result
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        await repo.log_audit(
            session,
            user_id=user_id,
            session_id=None,
            action_type="tool_execute",
            tool_name=name,
            tool_args=args,
            success=False,
            error_msg=str(e),
        )
        return {"error": str(e), "tool": name}
