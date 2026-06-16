"""Tool registry package.

Each tool is a module that self-registers via the local `@tool` decorator
(see `_registry`). Importing this package imports every tool module below, so
their decorators populate `TOOLS` — there is no manual dict to edit.

Public API (unchanged from the old single-module `tools.py`):
    TOOLS, list_tools, get_tool, execute_tool, build_task_items, tool

`repo` and `get_memory_service` are re-exported here because tests monkeypatch
them through this namespace (e.g. `tools.repo.get_mom_action_items`,
`tools.get_memory_service`); the retrieve tool resolves `get_memory_service`
via this package at call time so those patches take effect.
"""
from __future__ import annotations

# Shared references exposed for tests' monkeypatch points + the retrieve tool.
from meeting.db import repositories as repo
from meeting.services.memory_service import get_memory_service

from meeting.services.tools._registry import (
    TOOLS,
    execute_tool,
    get_tool,
    list_tools,
    tool,
)

# Import each tool module so its @tool decorator registers it. Import order =
# the order tools are offered to the LLM (matches the legacy TOOLS dict order).
from meeting.services.tools import send_email as _send_email  # noqa: F401
from meeting.services.tools.create_task import build_agenda_task_items, build_task_items
from meeting.services.tools import create_task as _create_task  # noqa: F401
from meeting.services.tools import switch_meeting as _switch_meeting  # noqa: F401
from meeting.services.tools import retrieve as _retrieve  # noqa: F401
from meeting.services.tools import list_meetings as _list_meetings  # noqa: F401
from meeting.services.tools import list_recordings as _list_recordings  # noqa: F401
from meeting.services.tools import recording_mom as _recording_mom  # noqa: F401
from meeting.services.tools import search_transcript as _search_transcript  # noqa: F401

# Redmine MCP tools register DYNAMICALLY (network discovery) at app startup via
# load_and_register_redmine_tools — importing the module here only loads the
# defs (no network, no registration), so the tool set stays clean until wired.
from meeting.services.tools.redmine import (  # noqa: F401
    ensure_redmine_tools_registered,
    ensure_redmine_tools_with_key,
    is_write_tool,
    load_and_register_redmine_tools,
    register_redmine_tools,
)

__all__ = [
    "TOOLS",
    "tool",
    "list_tools",
    "get_tool",
    "execute_tool",
    "build_task_items",
    "build_agenda_task_items",
    "repo",
    "get_memory_service",
    "is_write_tool",
    "ensure_redmine_tools_registered",
    "ensure_redmine_tools_with_key",
    "load_and_register_redmine_tools",
    "register_redmine_tools",
]
