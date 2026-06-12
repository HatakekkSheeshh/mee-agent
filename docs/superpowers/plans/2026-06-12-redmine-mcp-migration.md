# Redmine via MCP Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Mee's chat agent direct Redmine access through the deployed MCP server, and demote the pm-agent A2A hop to an explicit opt-in.

**Architecture:** A thin streamable-http MCP client (`redmine_mcp_client.py`) authenticates to `MCP_REDMINE_URL` with `Bearer <REDMINE_API_KEY>`. Its 5 tools are registered into the existing local `@tool` registry (`tools/redmine.py`) with hardcoded schemas; writes are `side_effect=True` so the existing HITL machinery gates them. The `create_task` batch flow stops bridging to pm-agent and instead applies its approved items over MCP (`create_redmine_issue`). `classify_intent` is reworked so Redmine ops stay in the agent branch; `pm_task` fires only on explicit "pm-agent" mention.

**Tech Stack:** Python, LangGraph, the `mcp` Python SDK (`streamablehttp_client` / `ClientSession`), FastMCP server (remote, already deployed), pytest (async).

---

## Refinement to spec judgment-call #3 (READ FIRST)

The spec said `create_task` would gain an optional per-item `issue_id` so the LLM marks updates. During planning this proved to over-complicate `create_task` (its schema builds items from MoM server-side; it has no LLM-supplied `items` array). **Simpler model used here, same outcome:** `create_task` stays a batch **create** flow (apply = `create_redmine_issue` per item). The LLM "reconciles" by calling `list_redmine_issue` to inspect existing issues and then calling the single `update_redmine_issue` tool directly (one HITL approval) for anything that already exists. The apply loop still *honors* an `issue_id` on an item if one is ever present (defensive/future-proof), but `create_task` does not ask the LLM for it. Flag to the user if they specifically wanted issue_id-on-items batching.

## Known limitations baked into v1 (from the deployed MCP schema)

- `create_redmine_issue` **requires** `tracker` + `assigned_to` and has **no `due_date` param**. The apply loop defaults `tracker="Task"` and folds an item's `due_date` into the `description` as `"Hạn: <date>"`. Items with an empty assignee will likely error server-side; that error is reported per-item, not fatal.
- Batch apply performs N writes in one node → partial-failure is possible on a mid-batch crash (same risk pm-agent had). v1 reports per-item results; no rollback.

## File Structure

- Create: `meeting/services/redmine_mcp_client.py` — streamable-http MCP client + pure result parsing + `get_redmine_mcp_client()`.
- Create: `meeting/services/tools/redmine.py` — registers the 5 MCP tools into `TOOLS`.
- Create: `tests/meeting/test_redmine_mcp_client.py` — pure parse/extract tests.
- Create: `tests/meeting/test_tools_redmine.py` — registry/side_effect tests.
- Create: `tests/meeting/test_redmine_apply.py` — full-graph create_task→MCP apply test.
- Modify: `requirements.txt` — add `mcp`.
- Modify: `.env.example` — document `MCP_REDMINE_URL` / `REDMINE_API_KEY`.
- Modify: `meeting/services/tools/__init__.py` — import the redmine tool module.
- Modify: `meeting/services/__init__.py` — re-export `get_redmine_mcp_client`.
- Modify: `meeting/graphs/_chat_serde.py` — add `redmine_create_args` / `redmine_update_args` / `summarize_redmine_apply`.
- Modify: `meeting/graphs/chat_graph/agent.py` — replace the create_task→pm bridge with MCP apply; simplify `route_after_agent_execute`.
- Modify: `meeting/graphs/chat_graph/builder.py` — drop `pm_call` from the `agent_execute` edge map.
- Modify: `meeting/graphs/_chat_prompts.py` — rework `CLASSIFY_SYSTEM_PROMPT`; add Redmine guidance to `_agent_system_prompt`.
- Modify: `tests/meeting/test_reconcile_bridge.py` — retarget the now-removed pm bridge to the MCP-apply behavior.

---

### Task 1: Add the `mcp` dependency and document env

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Add the client dependency**

Append under the LangGraph block in `requirements.txt`:

```
# === Redmine MCP client (P2) ===
mcp>=1.25.0
```

- [ ] **Step 2: Install it**

Run: `venv/bin/pip install -r requirements.txt`
Expected: installs `mcp` (and its deps) with no resolver conflict.

- [ ] **Step 3: Document env (real values stay in .env)**

Append to `.env.example`:

```
# === Redmine MCP (P2) ===
# Mee talks to Redmine directly via the deployed MCP server. The Bearer token
# IS the Redmine API key. Real values live in .env (do not commit them).
MCP_REDMINE_URL=https://mcp-redmine.vngcloud.vn/mcp
REDMINE_API_KEY=
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "chore(redmine-mcp): add mcp client dep + document env"
```

---

### Task 2: The MCP client

**Files:**
- Create: `meeting/services/redmine_mcp_client.py`
- Test: `tests/meeting/test_redmine_mcp_client.py`

- [ ] **Step 1: Write the failing tests (pure parsing)**

Create `tests/meeting/test_redmine_mcp_client.py`:

```python
"""Pure result-parsing for the Redmine MCP client (no network)."""
from __future__ import annotations

from types import SimpleNamespace

from meeting.services import redmine_mcp_client as rc


def _text_block(text):
    # Mirrors mcp.types.TextContent enough for _extract_text's isinstance check
    # via duck-typing: _extract_text falls back to getattr(block, "text", None).
    return SimpleNamespace(text=text)


def test_extract_text_concatenates_text_blocks():
    blocks = [_text_block("hello "), _text_block("world")]
    assert rc._extract_text(blocks) == "hello world"


def test_parse_result_prefers_structured_content():
    result = SimpleNamespace(
        isError=False,
        structuredContent={"issues": [{"id": 1}]},
        content=[],
    )
    assert rc._parse_call_result(result) == {"issues": [{"id": 1}]}


def test_parse_result_unwraps_sole_result_key():
    result = SimpleNamespace(
        isError=False, structuredContent={"result": [1, 2, 3]}, content=[]
    )
    assert rc._parse_call_result(result) == [1, 2, 3]


def test_parse_result_error_returns_error_dict():
    result = SimpleNamespace(
        isError=True, structuredContent=None, content=[_text_block("boom")]
    )
    assert rc._parse_call_result(result) == {"error": "boom"}


def test_parse_result_text_json_fallback():
    result = SimpleNamespace(
        isError=False, structuredContent=None, content=[_text_block('{"ok": 1}')]
    )
    assert rc._parse_call_result(result) == {"ok": 1}


def test_parse_result_non_json_text_wrapped_in_message():
    result = SimpleNamespace(
        isError=False, structuredContent=None, content=[_text_block("just words")]
    )
    assert rc._parse_call_result(result) == {"message": "just words"}


def test_parse_result_empty_returns_empty_dict():
    result = SimpleNamespace(isError=False, structuredContent=None, content=[])
    assert rc._parse_call_result(result) == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_mcp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: meeting.services.redmine_mcp_client`.

- [ ] **Step 3: Implement the client**

Create `meeting/services/redmine_mcp_client.py`:

```python
"""Redmine MCP client — streamable-http transport to the deployed MCP server.

Simplified port of pm-agent's src/mcp_server/mcp_http_client.py. Mee uses a
single env REDMINE_API_KEY as the Bearer token (the token IS the Redmine API
key; the server validates it against /users/current.json). No per-user auth.

A fresh streamable-http session is opened per tool call (sessions are cheap and
the key is fixed). Result parsing prefers FastMCP's structuredContent, unwraps
its {"result": ...} wrapper, surfaces isError as {"error": ...}, and falls back
to text→JSON.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _extract_text(content_blocks: list) -> str:
    """Concatenate text from a CallToolResult.content list."""
    if not content_blocks:
        return ""
    parts: list[str] = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _parse_call_result(result: Any) -> dict:
    """Normalize an mcp CallToolResult into a plain dict (pure; unit-tested)."""
    if getattr(result, "isError", False):
        return {"error": _extract_text(getattr(result, "content", None) or []) or "Unknown MCP tool error"}

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if set(structured.keys()) == {"result"}:
            return {"result_value": structured["result"]} if not isinstance(
                structured["result"], (dict, list)
            ) else structured["result"]
        return structured

    text = _extract_text(getattr(result, "content", None) or [])
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}


class RedmineMcpClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        if not base_url:
            raise ValueError("MCP_REDMINE_URL is not configured")
        url = base_url.rstrip("/")
        if not url.endswith("/mcp"):
            url = f"{url}/mcp"
        self._url = url
        self._api_key = api_key
        self._timeout = timeout

    @asynccontextmanager
    async def _session(self):
        # Imported lazily so importing this module (and the whole services
        # package, which conftest does) never requires `mcp` to be installed
        # unless a Redmine tool is actually invoked.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with streamablehttp_client(self._url, headers=headers, timeout=self._timeout) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def call_tool(self, name: str, arguments: dict) -> dict:
        logger.info("[redmine-mcp] call_tool %s args=%s", name, arguments)
        try:
            async with self._session() as session:
                result = await session.call_tool(name, arguments)
        except Exception as e:  # transport / auth / server error
            logger.exception("[redmine-mcp] call_tool %s failed", name)
            return {"error": f"redmine mcp error: {e}"}
        return _parse_call_result(result)


_singleton: Optional[RedmineMcpClient] = None


def get_redmine_mcp_client() -> RedmineMcpClient:
    """Lazy env singleton (mirrors get_pm_agent_client)."""
    global _singleton
    if _singleton is None:
        _singleton = RedmineMcpClient(
            base_url=os.getenv("MCP_REDMINE_URL", ""),
            api_key=os.getenv("REDMINE_API_KEY", ""),
        )
    return _singleton
```

> Note on `_parse_call_result`: the sole-`result`-key branch returns the inner
> value directly when it is a dict/list (matching pm-agent), and wraps a scalar
> as `{"result_value": ...}` so the function's return type stays `dict`. The
> test `test_parse_result_unwraps_sole_result_key` passes a list, exercising the
> direct-return path.

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_mcp_client.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/redmine_mcp_client.py tests/meeting/test_redmine_mcp_client.py
git commit -m "feat(redmine-mcp): streamable-http MCP client + result parsing"
```

---

### Task 3: Register the 5 MCP tools into the local registry

**Files:**
- Create: `meeting/services/tools/redmine.py`
- Modify: `meeting/services/tools/__init__.py`
- Modify: `meeting/services/__init__.py`
- Test: `tests/meeting/test_tools_redmine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/meeting/test_tools_redmine.py`:

```python
"""The 5 Redmine MCP tools register with correct side_effect flags + schemas."""
from __future__ import annotations

import meeting.services.tools as tools


READS = {"list_redmine_issue", "get_redmine_projects"}
WRITES = {"create_redmine_issue", "update_redmine_issue", "create_redmine_subtask"}


def test_all_five_redmine_tools_registered():
    names = {t["name"] for t in tools.list_tools()}
    assert READS | WRITES <= names


def test_reads_are_not_side_effect():
    for n in READS:
        assert tools.get_tool(n)["side_effect"] is False


def test_writes_are_side_effect():
    for n in WRITES:
        assert tools.get_tool(n)["side_effect"] is True


def test_create_issue_required_fields():
    schema = tools.get_tool("create_redmine_issue")["schema"]
    assert set(schema["required"]) == {"project_name", "subject", "tracker", "assigned_to"}


def test_update_issue_required_fields():
    schema = tools.get_tool("update_redmine_issue")["schema"]
    assert set(schema["required"]) == {"issue_id", "project_name"}


async def test_executor_proxies_to_mcp_client(monkeypatch):
    captured = {}

    class _FakeClient:
        async def call_tool(self, name, arguments):
            captured["name"] = name
            captured["args"] = arguments
            return {"ok": True}

    monkeypatch.setattr(
        "meeting.services.tools.redmine.get_redmine_mcp_client", lambda: _FakeClient()
    )
    out = await tools.execute_tool(
        "list_redmine_issue", {"project_name": "GIP"}, session=None, user_id=None
    )
    assert out == {"ok": True}
    assert captured == {"name": "list_redmine_issue", "args": {"project_name": "GIP"}}
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_tools_redmine.py -v`
Expected: FAIL — tools not registered (`get_tool` returns `None`).

- [ ] **Step 3: Implement the tool module**

Create `meeting/services/tools/redmine.py`:

```python
"""Redmine MCP tools — thin proxies to the deployed MCP server.

Each executor forwards to get_redmine_mcp_client().call_tool(<name>, args).
Writes are side_effect=True so the chat graph's HITL machinery gates them.
Schemas are hardcoded (the deployed server's 5-tool surface is stable +
documented) so no network call is needed at import/registration time.
"""
from __future__ import annotations

import uuid

from meeting.services.redmine_mcp_client import get_redmine_mcp_client
from meeting.services.tools._registry import tool

_TRACKERS = "e.g. Bug, Feature, Task"


def _proxy(name: str):
    async def _exec(args: dict, *, session, user_id) -> dict:
        return await get_redmine_mcp_client().call_tool(name, dict(args or {}))

    _exec.__name__ = f"redmine_{name}"
    return _exec


tool(
    name="get_redmine_projects",
    description="List Redmine projects the configured key can access. No arguments.",
    side_effect=False,
    schema={"type": "object", "properties": {}},
)(_proxy("get_redmine_projects"))

tool(
    name="list_redmine_issue",
    description=(
        "List Redmine issues in a project. Use to inspect existing issues "
        "(e.g. before creating/updating, or to answer 'issues of X / overdue')."
    ),
    side_effect=False,
    schema={
        "type": "object",
        "properties": {
            "project_name": {"type": "string", "description": "Redmine project name"},
            "assigned_to": {"type": "string", "description": "Filter by assignee (Redmine login or name)"},
            "status": {"type": "string", "description": "Filter by status, e.g. New/In Progress/Closed"},
        },
        "required": ["project_name"],
    },
)(_proxy("list_redmine_issue"))

tool(
    name="create_redmine_issue",
    description=(
        "Create ONE Redmine issue the user explicitly dictated. For syncing a "
        "whole meeting's action items, use create_task instead. Requires approval."
    ),
    side_effect=True,
    schema={
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "subject": {"type": "string"},
            "tracker": {"type": "string", "description": f"Tracker type ({_TRACKERS})"},
            "assigned_to": {"type": "string", "description": "Redmine login or name"},
            "status": {"type": "string"},
            "priority": {"type": "string"},
            "description": {"type": "string"},
            "target_version": {"type": "string"},
            "category": {"type": "string"},
        },
        "required": ["project_name", "subject", "tracker", "assigned_to"],
    },
)(_proxy("create_redmine_issue"))

tool(
    name="update_redmine_issue",
    description=(
        "Update an existing Redmine issue by id (status, assignee, notes, …). "
        "Use this to reconcile an action item that already has an issue. Requires approval."
    ),
    side_effect=True,
    schema={
        "type": "object",
        "properties": {
            "issue_id": {"type": "string", "description": "Numeric Redmine issue id"},
            "project_name": {"type": "string"},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "tracker": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "string"},
            "assigned_to": {"type": "string"},
            "notes": {"type": "string"},
            "target_version": {"type": "string"},
            "category": {"type": "string"},
        },
        "required": ["issue_id", "project_name"],
    },
)(_proxy("update_redmine_issue"))

tool(
    name="create_redmine_subtask",
    description="Create a subtask under a parent Redmine issue. Requires approval.",
    side_effect=True,
    schema={
        "type": "object",
        "properties": {
            "parent_issue_id": {"type": "string"},
            "project_name": {"type": "string"},
            "subject": {"type": "string"},
            "assigned_to": {"type": "string"},
            "tracker": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "string"},
            "description": {"type": "string"},
            "target_version": {"type": "string"},
            "category": {"type": "string"},
        },
        "required": ["parent_issue_id", "project_name", "subject", "assigned_to"],
    },
)(_proxy("create_redmine_subtask"))

# Silence "imported but unused" — uuid kept for parity with other tool modules.
_ = uuid
```

> Remove the trailing `uuid`/`_ = uuid` lines if your linter prefers; they are
> only there to match the import style of sibling tool modules. If you drop
> them, also drop `import uuid`.

- [ ] **Step 4: Register the module at import time**

In `meeting/services/tools/__init__.py`, add after the existing tool imports
(after the `search_transcript` import line):

```python
from meeting.services.tools import redmine as _redmine  # noqa: F401
```

- [ ] **Step 5: Re-export the client accessor**

In `meeting/services/__init__.py`, add to the imports:

```python
from meeting.services.redmine_mcp_client import RedmineMcpClient, get_redmine_mcp_client
```

and add `"RedmineMcpClient"` and `"get_redmine_mcp_client"` to `__all__`.

- [ ] **Step 6: Run to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_tools_redmine.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Commit**

```bash
git add meeting/services/tools/redmine.py meeting/services/tools/__init__.py meeting/services/__init__.py tests/meeting/test_tools_redmine.py
git commit -m "feat(redmine-mcp): register 5 Redmine tools in the local registry"
```

---

### Task 4: Pure helpers for mapping template items → Redmine args

**Files:**
- Modify: `meeting/graphs/_chat_serde.py`
- Test: `tests/meeting/test_redmine_apply.py` (parse-helper section)

- [ ] **Step 1: Write the failing tests**

Create `tests/meeting/test_redmine_apply.py` with this first section:

```python
"""create_task → MCP apply: item→args mapping, summary, and full-graph flow."""
from __future__ import annotations

from meeting.graphs import _chat_serde as serde


def test_create_args_defaults_tracker_and_folds_due_date():
    args = serde.redmine_create_args(
        "GIP", {"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01/2026", "description": "schema"}
    )
    assert args["project_name"] == "GIP"
    assert args["subject"] == "viết migration"
    assert args["tracker"] == "Task"           # default when item has none
    assert args["assigned_to"] == "Hiếu"
    assert "schema" in args["description"]
    assert "Hạn: 10/01/2026" in args["description"]


def test_create_args_respects_explicit_tracker():
    args = serde.redmine_create_args("GIP", {"subject": "x", "tracker": "Bug"})
    assert args["tracker"] == "Bug"


def test_update_args_includes_only_present_fields():
    args = serde.redmine_update_args("GIP", {"subject": "new", "due_date": "12/01"}, "123")
    assert args["issue_id"] == "123"
    assert args["project_name"] == "GIP"
    assert args["subject"] == "new"
    assert "Hạn: 12/01" in args["notes"]
    assert "assigned_to" not in args          # absent in item → omitted


def test_summary_counts_ok_and_lists_failures():
    results = [
        {"subject": "a", "result": {"id": 1}},
        {"subject": "b", "result": {"error": "no assignee"}},
    ]
    text = serde.summarize_redmine_apply("GIP", results)
    assert "1/2" in text
    assert "GIP" in text
    assert "b" in text and "no assignee" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'redmine_create_args'`.

- [ ] **Step 3: Implement the helpers**

Append to `meeting/graphs/_chat_serde.py`:

```python
def _fold_due_date(description: str, due_date: str) -> str:
    desc = (description or "").strip()
    due = (due_date or "").strip()
    if due:
        desc = (desc + f"\nHạn: {due}").strip()
    return desc


def redmine_create_args(project: str, item: dict) -> dict:
    """Map a create_task template item → create_redmine_issue args.

    The deployed MCP create tool REQUIRES tracker + assigned_to and has no
    due_date param, so default tracker='Task' and fold any deadline into the
    description.
    """
    return {
        "project_name": project,
        "subject": item.get("subject", ""),
        "tracker": item.get("tracker") or "Task",
        "assigned_to": item.get("assignee", ""),
        "description": _fold_due_date(item.get("description", ""), item.get("due_date", "")),
    }


def redmine_update_args(project: str, item: dict, issue_id: str) -> dict:
    """Map a template item → update_redmine_issue args (only present fields)."""
    args: dict = {"issue_id": str(issue_id), "project_name": project}
    if item.get("subject"):
        args["subject"] = item["subject"]
    if item.get("assignee"):
        args["assigned_to"] = item["assignee"]
    notes = _fold_due_date(item.get("description", ""), item.get("due_date", ""))
    if notes:
        args["notes"] = notes
    return args


def summarize_redmine_apply(project: str, results: list[dict]) -> str:
    """Vietnamese summary of a batch apply ({subject, result} per item)."""
    failed = [r for r in results if (r.get("result") or {}).get("error")]
    ok_count = len(results) - len(failed)
    lines = [f"Đã đồng bộ {ok_count}/{len(results)} việc lên Redmine (dự án {project})."]
    for r in failed:
        lines.append(f"- ❌ {r.get('subject', '')}: {(r.get('result') or {}).get('error')}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py -v`
Expected: PASS (4 tests in this section).

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/_chat_serde.py tests/meeting/test_redmine_apply.py
git commit -m "feat(redmine-mcp): pure item→Redmine-args + apply-summary helpers"
```

---

### Task 5: Rewrite the create_task apply path (pm bridge → MCP apply)

**Files:**
- Modify: `meeting/graphs/chat_graph/agent.py:339-416`
- Modify: `meeting/graphs/chat_graph/builder.py:70-74`
- Test: `tests/meeting/test_redmine_apply.py` (full-graph section)

- [ ] **Step 1: Write the failing full-graph test**

Append to `tests/meeting/test_redmine_apply.py`:

```python
import uuid
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from meeting.graphs import chat_graph
from meeting.graphs.chat_graph import (
    ChatState,
    make_agent,
    make_agent_approve,
    make_agent_execute,
    make_agent_tools,
    route_after_agent,
    route_after_agent_execute,
    route_after_agent_tools,
)

MID = "11111111-1111-1111-1111-111111111111"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION = object()


def _resp_tool(calls):
    tcs = [SimpleNamespace(id=c["id"], type="function",
                           function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
           for c in calls]
    msg = SimpleNamespace(content=None, tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


class _FakeLLM:
    def __init__(self, scripted):
        self._s = list(scripted); self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        i = len(self.calls); self.calls.append(kw); return self._s[i]


class _FakeExec:
    def __init__(self): self.calls = []
    async def __call__(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args})
        return {"id": 100 + len(self.calls)}   # fake created issue id, no "error"


CREATE_TASK_SPEC = {
    "name": "create_task", "description": "make tasks", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "title": {"type": "string"}}},
}


class FakeToolset:
    def __init__(self): self.exec = _FakeExec()
    def list_tools(self): return [CREATE_TASK_SPEC]
    def get_tool(self, n): return CREATE_TASK_SPEC if n == "create_task" else None
    async def execute_tool(self, name, args, *, session, user_id):
        return await self.exec(name, args, session=session, user_id=user_id)
    def build_task_items(self, items):
        from meeting.services import build_task_items as real
        return real(items)


def _build(llm, checkpointer, tools):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm, tools=tools))
    g.add_node("agent_tools", make_agent_tools(SESSION, tools=tools))
    g.add_node("agent_approve", make_agent_approve(tools=tools))
    g.add_node("agent_execute", make_agent_execute(SESSION, tools=tools))
    g.add_node("save_reply", lambda s: {})
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route_after_agent,
                            {"agent_tools": "agent_tools", "save_reply": "save_reply"})
    g.add_conditional_edges("agent_tools", route_after_agent_tools,
                            {"agent": "agent", "agent_approve": "agent_approve"})
    g.add_edge("agent_approve", "agent_execute")
    g.add_conditional_edges("agent_execute", route_after_agent_execute,
                            {"agent": "agent", "save_reply": "save_reply"})
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _initial(msg):
    return {"session_id": "s", "user_id": str(UID), "user_message": msg,
            "resolved_meeting_id": MID,
            "meeting_context": {"id": MID, "title": "AI Innovation Project"}}


async def _interrupted(graph, cfg):
    snap = await graph.aget_state(cfg); return bool(snap.next)


async def test_create_task_applies_over_mcp(monkeypatch):
    ts = FakeToolset()

    async def fake_items(session, mid):
        return [
            {"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"},
            {"pic": "Mai", "deadline": "", "item": "POC caching"},
        ]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([_resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}])])
    graph = _build(llm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "apply"}}

    # Turn 1: agent calls create_task → HITL interrupt (the only approval)
    await graph.ainvoke(_initial("đồng bộ các việc trong họp lên Redmine"), cfg)
    assert await _interrupted(graph, cfg)
    assert ts.exec.calls == []   # nothing applied before approval

    # Approve → apply over MCP (one create_redmine_issue per item), terminal
    result = await graph.ainvoke(Command(resume={"action": "approved"}), cfg)
    assert not await _interrupted(graph, cfg)
    applied = [c for c in ts.exec.calls if c["name"] == "create_redmine_issue"]
    assert len(applied) == 2
    assert {c["args"]["subject"] for c in applied} == {"viết migration", "POC caching"}
    assert applied[0]["args"]["tracker"] == "Task"
    assert "2/2" in result["final_reply"]
    assert result["tool_result"]["status"] == "redmine_apply"


async def test_reject_create_task_applies_nothing(monkeypatch):
    ts = FakeToolset()

    async def fake_items(session, mid):
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([_resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}])])
    graph = _build(llm, MemorySaver(), ts)
    cfg = {"configurable": {"thread_id": "apply-reject"}}

    await graph.ainvoke(_initial("đồng bộ lên Redmine"), cfg)
    result = await graph.ainvoke(Command(resume={"action": "rejected", "reason": "thôi"}), cfg)
    assert not await _interrupted(graph, cfg)
    assert ts.exec.calls == []
    assert result["final_reply"] == chat_graph.REJECT_REPLY
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py -v`
Expected: FAIL — the approved create_task still routes to `reconcile`/`pm_call`; `test_create_task_applies_over_mcp` fails (no `create_redmine_issue` calls; `final_reply` lacks "2/2").

- [ ] **Step 3: Replace the bridge with MCP apply in `agent_execute`**

In `meeting/graphs/chat_graph/agent.py`, change the import on line 22-29 — remove `_reconcile_payloads`, add the new helpers:

```python
from meeting.graphs._chat_serde import (
    _json,
    _last_assistant_text,
    _parse_tool_args,
    _seed_agent_messages,
    _tc_to_dict,
    redmine_create_args,
    redmine_update_args,
    summarize_redmine_apply,
)
```

Replace the approved-`create_task` block (currently lines 338-362) with:

```python
        # Approved create_task → apply the batch over the Redmine MCP. One HITL
        # approval gated the whole batch (above); execution is a deterministic
        # loop. An item carrying issue_id is an update; otherwise a create.
        if action == "approved" and name == "create_task":
            template = dict(args)  # {project, items}
            if decision.get("edited_args"):
                template.update(decision["edited_args"])
            project = template.get("project", "")
            items = template.get("items", []) or []
            results = []
            for it in items:
                issue_id = str(it.get("issue_id") or "").strip()
                if issue_id:
                    res = await ts.execute_tool(
                        "update_redmine_issue",
                        redmine_update_args(project, it, issue_id),
                        session=session, user_id=user_id,
                    )
                else:
                    res = await ts.execute_tool(
                        "create_redmine_issue",
                        redmine_create_args(project, it),
                        session=session, user_id=user_id,
                    )
                results.append({"subject": it.get("subject", ""), "issue_id": issue_id, "result": res})
            logger.info("[Node agent_execute] create_task → MCP apply (%d item(s))", len(items))
            return {
                "pending_tool": None,
                "user_decision": None,
                "agent_route": "finish",
                "tool_result": {
                    "status": "redmine_apply", "project": project,
                    "count": len(items), "results": results,
                },
                "final_reply": summarize_redmine_apply(project, results),
            }
```

- [ ] **Step 4: Simplify `route_after_agent_execute`**

Replace the function (currently lines 407-416) with:

```python
def route_after_agent_execute(state: ChatState) -> Literal["agent", "save_reply"]:
    """Finish the turn after a rejected side-effect tool or a completed batch
    apply (agent_route="finish"); otherwise loop back to the agent (normal
    approved single side-effect tools)."""
    return "save_reply" if state.get("agent_route") == "finish" else "agent"
```

- [ ] **Step 5: Drop the dead `pm_call` edge from `agent_execute`**

In `meeting/graphs/chat_graph/builder.py`, change the `agent_execute` conditional
edges (lines 70-74) to:

```python
    g.add_conditional_edges(
        "agent_execute",
        route_after_agent_execute,
        {"agent": "agent", "save_reply": "save_reply"},
    )
```

(The `pm_call` node stays — it is still reachable from `classify_intent` via the
opt-in pm path.)

- [ ] **Step 6: Run the new test to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py -v`
Expected: PASS (all sections).

- [ ] **Step 7: Commit**

```bash
git add meeting/graphs/chat_graph/agent.py meeting/graphs/chat_graph/builder.py tests/meeting/test_redmine_apply.py
git commit -m "feat(redmine-mcp): apply approved create_task over MCP, drop pm bridge default"
```

---

### Task 6: Fix the now-broken reconcile-bridge test

**Files:**
- Modify: `tests/meeting/test_reconcile_bridge.py`

The pm bridge is no longer the create_task default, so three tests there are now
wrong. Update them; keep the `_reconcile_text` and `_build_reconcile_template`
tests (those helpers are unchanged).

- [ ] **Step 1: Run the suite to see the failures**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -v`
Expected: FAIL — `test_route_after_agent_execute_reconcile_goes_to_pm` and
`test_full_bridge_create_task_to_reconcile` fail (route no longer returns
`pm_call`; create_task no longer hands off to pm).

- [ ] **Step 2: Replace the route test**

Replace `test_route_after_agent_execute_reconcile_goes_to_pm` (lines 124-125) with:

```python
def test_route_after_agent_execute_finish_goes_to_save_reply():
    assert chat_graph.route_after_agent_execute({"agent_route": "finish"}) == "save_reply"
```

- [ ] **Step 3: Delete the obsolete full pm-bridge test**

Delete `test_full_bridge_create_task_to_reconcile` (lines 281-324). Its
replacement is `test_create_task_applies_over_mcp` in `test_redmine_apply.py`.
Keep `test_bridge_reject_gate1_no_handoff` but it no longer needs `_FakePm`
results to assert "never bridged" — leave it as-is (it already asserts
`pm.calls == []` and the canned reject reply, both still true).

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/meeting/test_reconcile_bridge.py
git commit -m "test(redmine-mcp): retarget reconcile-bridge tests to MCP apply"
```

---

### Task 7: Rework `classify_intent` (pm_task → opt-in)

**Files:**
- Modify: `meeting/graphs/_chat_prompts.py:9-54`
- Test: `tests/meeting/test_reconcile_bridge.py` (the classify-prompt assertion) + new assertions

- [ ] **Step 1: Write the failing test**

Append to `tests/meeting/test_redmine_apply.py`:

```python
def test_classify_prompt_routes_redmine_ops_to_agent():
    src = chat_graph.CLASSIFY_SYSTEM_PROMPT
    # Redmine issue ops now default to the agent (it owns the MCP tools).
    assert "list_redmine_issue" in src or "MCP" in src
    # pm_task is now explicit-opt-in only.
    assert "pm-agent" in src
    # Example: a plain Redmine create now routes to agent, not pm_task.
    assert '"intent":"agent"' in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py::test_classify_prompt_routes_redmine_ops_to_agent -v`
Expected: FAIL — current prompt has no `pm-agent` opt-in language / `list_redmine_issue`.

- [ ] **Step 3: Replace `CLASSIFY_SYSTEM_PROMPT`**

In `meeting/graphs/_chat_prompts.py`, replace the whole `CLASSIFY_SYSTEM_PROMPT`
assignment (lines 10-54) with:

```python
CLASSIFY_SYSTEM_PROMPT = (
    "Bạn là bộ định tuyến cho trợ lý cuộc họp Mee. Phân loại tin nhắn user và "
    'trả về CHỈ JSON {"intent": "pm_task" | "agent", "grounding": "required" | "auto"} '
    "(không markdown, không giải thích).\n\n"
    'MẶC ĐỊNH là "agent". Agent có sẵn công cụ Redmine qua MCP '
    "(list_redmine_issue, create_redmine_issue, update_redmine_issue, "
    "get_redmine_projects), nên MỌI thao tác Redmine thông thường đều thuộc "
    '"agent". CHỈ chọn "pm_task" khi user nói RÕ RÀNG muốn dùng "pm-agent" '
    "(trợ lý đối chiếu Redmine riêng). Nếu phân vân → chọn \"agent\".\n\n"
    'TRƯỜNG "grounding" — bắt agent đọc dữ liệu thật trước khi trả lời:\n'
    '  • "required" khi user hỏi về NỘI DUNG / DỮ LIỆU CUỘC HỌP có thật: tóm tắt '
    "một phiên/Meeting N, biên bản (MoM), quyết định, blocker, ai nói gì, việc/"
    "action item của một người, liệt kê recording/phiên — câu trả lời PHẢI lấy "
    "từ dữ liệu cuộc họp (không bịa).\n"
    '  • "auto" cho chào hỏi/chit-chat, câu hỏi chung, hoặc yêu cầu hành động '
    "(tạo task, gửi email, thao tác Redmine) — không cần đọc nội dung trước. "
    'Nếu phân vân → chọn "auto".\n\n'
    '"agent" — gồm:\n'
    "  • mọi nội dung/dữ liệu cuộc họp (tóm tắt, MoM, quyết định, blocker, "
    "recording, transcript, action item theo người)\n"
    "  • mọi thao tác Redmine THÔNG THƯỜNG: liệt kê issue (overdue/được giao/"
    "theo project), tạo/cập nhật issue, tạo subtask — agent gọi công cụ MCP\n"
    "  • đồng bộ action item của cuộc họp lên Redmine — agent tự dựng danh sách "
    "việc rồi tạo issue qua MCP\n"
    "  • tạo task nội bộ, gửi email, tìm trong transcript\n\n"
    '"pm_task" — CHỈ khi user nói RÕ RÀNG muốn dùng pm-agent:\n'
    "  • có cụm như 'dùng pm-agent', 'pm-agent', 'đối chiếu bằng pm-agent', "
    "'nhờ pm-agent'\n\n"
    "Ví dụ:\n"
    '  "List the recorded_id in AI Innovation Project" → {"intent":"agent","grounding":"required"}\n'
    '  "what tasks does Hieu need to do?" → {"intent":"agent","grounding":"required"}\n'
    '  "tóm tắt phiên 1 / Meeting 2" → {"intent":"agent","grounding":"required"}\n'
    '  "liệt kê issue trong project AI Innovation Project" → {"intent":"agent","grounding":"auto"}\n'
    '  "liệt kê issue overdue của tôi" → {"intent":"agent","grounding":"auto"}\n'
    '  "tạo issue trên Redmine cho việc deploy v1" → {"intent":"agent","grounding":"auto"}\n'
    '  "cập nhật trạng thái issue #123" → {"intent":"agent","grounding":"auto"}\n'
    '  "đồng bộ các việc trong biên bản họp lên Redmine" → {"intent":"agent","grounding":"auto"}\n'
    '  "tạo task cho Mai deploy v1" → {"intent":"agent","grounding":"auto"}\n'
    '  "chào bạn / bạn là ai?" → {"intent":"agent","grounding":"auto"}\n'
    '  "nhờ pm-agent đối chiếu các issue của dự án" → {"intent":"pm_task","grounding":"auto"}\n'
    '  "dùng pm-agent để đồng bộ" → {"intent":"pm_task","grounding":"auto"}'
)
```

- [ ] **Step 4: Run the classify tests to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py::test_classify_prompt_routes_redmine_ops_to_agent tests/meeting/test_reconcile_bridge.py::test_classify_prompt_routes_meeting_tasks_to_agent -v`
Expected: PASS. (The existing `test_classify_prompt_routes_meeting_tasks_to_agent`
still passes: the prompt keeps "biên bản", "agent", and "lên Redmine".)

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/_chat_prompts.py tests/meeting/test_redmine_apply.py
git commit -m "feat(redmine-mcp): route Redmine ops to agent, pm_task opt-in only"
```

---

### Task 8: Add Redmine guidance to the agent system prompt

**Files:**
- Modify: `meeting/graphs/_chat_prompts.py:67-123` (the `_agent_system_prompt` return)
- Test: `tests/meeting/test_redmine_apply.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/meeting/test_redmine_apply.py`:

```python
def test_agent_prompt_has_redmine_guidance():
    prompt = chat_graph._agent_system_prompt({"meeting_context": {"title": "GIP"}})
    assert "list_redmine_issue" in prompt
    assert "update_redmine_issue" in prompt
    # create_task vs create_redmine_issue disambiguation present
    assert "create_redmine_issue" in prompt and "create_task" in prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py::test_agent_prompt_has_redmine_guidance -v`
Expected: FAIL — prompt has no Redmine tool guidance.

- [ ] **Step 3: Insert the Redmine guidance**

In `_agent_system_prompt`, insert these bullet lines into the returned rules
block, immediately BEFORE the line that begins
`"- Khi user muốn chuyển sang project/cuộc họp khác"` (the `switch_meeting`
bullet):

```python
        "- REDMINE (qua công cụ MCP): để XEM/LIỆT KÊ issue (overdue, được giao, "
        "theo project) → gọi `list_redmine_issue`. Để tạo MỘT issue user đọc rõ "
        "→ `create_redmine_issue`. Để cập nhật issue đã có (đổi trạng thái, người "
        "phụ trách, ghi chú) → `update_redmine_issue` (gọi `list_redmine_issue` "
        "trước để lấy đúng issue_id). Các thao tác ghi này cần DUYỆT.\n"
        "- PHÂN BIỆT `create_task` vs `create_redmine_issue`: `create_task` dùng "
        "để ĐỒNG BỘ NHIỀU việc từ biên bản một cuộc họp (hệ thống tự dựng danh "
        "sách rồi tạo issue hàng loạt sau khi duyệt); `create_redmine_issue` chỉ "
        "cho MỘT issue đơn lẻ user đọc rõ. Khi đồng bộ cả cuộc họp → `create_task`.\n"
        "- Trường Redmine (`project_name`, `tracker`, `assigned_to`) là tên/định "
        "danh phía Redmine; truyền đúng tên project và người phụ trách.\n"
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_redmine_apply.py::test_agent_prompt_has_redmine_guidance -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/_chat_prompts.py tests/meeting/test_redmine_apply.py
git commit -m "feat(redmine-mcp): agent prompt Redmine guidance + create_task disambiguation"
```

---

### Task 9: Full suite + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole meeting suite**

Run: `venv/bin/python -m pytest tests/meeting/ -q`
Expected: PASS, no regressions. If `mcp` import errors surface from unrelated
modules, confirm the lazy-import in `redmine_mcp_client._session` is intact (the
package must import without `mcp` installed at collection time — though Task 1
installed it).

- [ ] **Step 2: Manual smoke (live, optional — needs real .env + network)**

Run (interactively, via `!` in the session so secrets stay local):
`! ECC_GATEGUARD=off venv/bin/python -c "import asyncio; from dotenv import load_dotenv; load_dotenv(override=True, interpolate=False); from meeting.services import get_redmine_mcp_client; print(asyncio.run(get_redmine_mcp_client().call_tool('get_redmine_projects', {})))"`
Expected: a dict of projects (not `{"error": ...}`). Confirms env + auth + transport.

- [ ] **Step 3: Final verification note**

Confirm in the PR description: (a) which tests pass, (b) that the live smoke was
run or explicitly skipped, (c) pm-agent branch still reachable via explicit
"pm-agent" phrasing.

---

## Self-Review (completed)

- **Spec coverage:** client (Task 2), registry+side_effect (Task 3), batch apply
  rewrite (Tasks 4-5), classify rework (Task 7), agent prompt (Task 8), config/deps
  (Task 1), tests across all, pm-agent kept opt-in (Tasks 5-7). The broken existing
  test is explicitly fixed (Task 6).
- **Refinement flagged:** judgment-call #3 (issue_id-on-items) simplified to
  "updates via the single update tool"; called out at top + Task 5.
- **Type/name consistency:** `redmine_create_args` / `redmine_update_args` /
  `summarize_redmine_apply` defined in Task 4 and used verbatim in Task 5;
  `get_redmine_mcp_client` defined in Task 2, exported in Task 3, used in Task 3's
  tool module; `route_after_agent_execute` new signature (Task 5) matches the
  builder edge map (Task 5 Step 5) and the updated test (Task 6).
- **Placeholder scan:** none — every code step is concrete.
