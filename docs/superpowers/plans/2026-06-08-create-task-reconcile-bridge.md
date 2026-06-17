# create_task → pm-agent reconcile bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the unified agent's `create_task` build a task template from the meeting MoM, let the user review it (editable project), then hand it to pm-agent's `redmine_reconcile` over the existing A2A loop — instead of the user creating Redmine issues by hand.

**Architecture:** `create_task` stays a side-effect tool. `agent_tools` builds the template `{project, items}` *before* the local approval (GATE 1). On approval, `agent_execute` builds a `kind="reconcile"` pm payload and routes into the existing `pm_call`/`pm_await`/`pm_reply` loop (GATE 2 = pm-agent's own write approval). Only one graph edge changes (`agent_execute → agent` becomes conditional).

**Tech Stack:** Python, LangGraph, pytest (asyncio auto-mode), httpx A2A client. Run tests with `venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-08-create-task-reconcile-bridge-design.md`

**Convention reminders:**
- Tests live in `tests/meeting/`; `pytest.ini` sets `asyncio_mode = auto` (no `@pytest.mark.asyncio` needed).
- GateGuard hook asks for facts before each Bash/Edit/Write — present them and retry, or set `ECC_GATEGUARD=off`.
- Commit only when the user asks (repo convention: conventional-commit messages).

---

### Task 1: Extract `build_task_items` helper in tools.py

Pure normalizer shared by `_exec_create_task` and the new template builder (DRY).

**Files:**
- Modify: `meeting/services/tools.py`
- Modify: `meeting/services/__init__.py` (export it)
- Test: `tests/meeting/test_tools_create_task.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/meeting/test_tools_create_task.py`:

```python
def test_build_task_items_normalizes_action_items():
    items = tools.build_task_items([
        {"pic": "Tuấn", "deadline": "06/06/2026", "item": "migration"},
        {"pic": "", "deadline": "", "item": ""},          # dropped (no item)
        {"pic": "Mai", "deadline": "Chưa xác định", "item": "POC caching"},
    ])
    assert len(items) == 2
    assert items[0] == {
        "subject": "migration", "assignee": "Tuấn",
        "due_date": "06/06/2026", "description": "",
    }
    assert items[1]["subject"] == "POC caching"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_tools_create_task.py::test_build_task_items_normalizes_action_items -v`
Expected: FAIL — `AttributeError: module 'meeting.services.tools' has no attribute 'build_task_items'`

- [ ] **Step 3: Add the helper in `meeting/services/tools.py`** (just above `_exec_create_task`):

```python
def build_task_items(action_items: list[dict]) -> list[dict]:
    """Normalize MoM action_items ({pic, deadline, item}) → task items
    ({subject, assignee, due_date, description}). Drops items without text."""
    return [
        {
            "subject": ai.get("item", ""),
            "assignee": ai.get("pic", ""),
            "due_date": ai.get("deadline", ""),
            "description": "",
        }
        for ai in action_items
        if ai.get("item")
    ]
```

- [ ] **Step 4: Refactor `_exec_create_task` to reuse it** — replace the MoM-branch list comprehension (the `tasks = [ {...} for ai in action_items if ai.get("item") ]` block) with:

```python
    tasks = build_task_items(action_items)
```

- [ ] **Step 5: Export from `meeting/services/__init__.py`** — add `build_task_items` to the import line from `.tools` and to `__all__` (match the existing `execute_tool, get_tool, list_tools` pattern).

- [ ] **Step 6: Run tests to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_tools_create_task.py -v`
Expected: PASS (all create_task tests, including the new one and the unchanged `test_create_task_from_mom_action_items`).

- [ ] **Step 7: Commit**

```bash
git add meeting/services/tools.py meeting/services/__init__.py tests/meeting/test_tools_create_task.py
git commit -m "refactor(tools): extract build_task_items normalizer"
```

---

### Task 2: `_reconcile_text` — reconcile message builder

**Files:**
- Modify: `meeting/graphs/chat_graph.py`
- Test: `tests/meeting/test_reconcile_bridge.py` (create)

- [ ] **Step 1: Write the failing test** — create `tests/meeting/test_reconcile_bridge.py`:

```python
"""create_task → pm-agent reconcile bridge (2026-06-08 spec)."""
from __future__ import annotations

import uuid

from meeting.graphs import chat_graph


def test_reconcile_text_lists_items_and_project():
    text = chat_graph._reconcile_text("GIP", [
        {"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01/2026"},
        {"subject": "POC caching", "assignee": "", "due_date": ""},
    ])
    assert "GIP" in text
    assert "viết migration" in text
    assert "Hiếu" in text
    assert "10/01/2026" in text
    assert "POC caching" in text


def test_reconcile_text_handles_blank_project():
    text = chat_graph._reconcile_text("", [{"subject": "x"}])
    assert "x" in text
    assert text  # non-empty even with no project
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_reconcile_text'`

- [ ] **Step 3: Implement in `meeting/graphs/chat_graph.py`** (in the "Unified tool-calling agent" section, near `_inject_meeting`):

```python
def _reconcile_text(project: str, items: list[dict]) -> str:
    """Phrase a reconcile request pm-agent's reconcile_check_info can parse:
    a target project + a numbered list of items."""
    header = f"Đối chiếu và tạo/cập nhật các công việc sau trên dự án {project or '(chưa rõ)'}:"
    lines = [header]
    for i, it in enumerate(items, 1):
        parts = [it.get("subject", "")]
        if it.get("assignee"):
            parts.append(f"phụ trách {it['assignee']}")
        if it.get("due_date"):
            parts.append(f"hạn {it['due_date']}")
        lines.append(f"{i}. " + " — ".join(p for p in parts if p))
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph.py tests/meeting/test_reconcile_bridge.py
git commit -m "feat(chat): reconcile message builder for pm handoff"
```

---

### Task 3: `_build_reconcile_template` — MoM/explicit → {project, items}

**Files:**
- Modify: `meeting/graphs/chat_graph.py`
- Test: `tests/meeting/test_reconcile_bridge.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/meeting/test_reconcile_bridge.py`:

```python
MID = "11111111-1111-1111-1111-111111111111"


async def test_build_template_from_mom_action_items(monkeypatch):
    async def fake_items(session, mid):
        assert mid == uuid.UUID(MID)
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]

    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    tpl = await chat_graph._build_reconcile_template(
        object(), {}, {"title": "AI Innovation Project"}, MID
    )
    assert tpl["project"] == "AI Innovation Project"   # default = meeting title
    assert tpl["items"][0]["subject"] == "viết migration"
    assert tpl["items"][0]["assignee"] == "Hiếu"


async def test_build_template_from_explicit_title():
    tpl = await chat_graph._build_reconcile_template(
        object(),
        {"title": "Deploy v1", "assignee": "Mai", "deadline": "06/06/2026"},
        {"title": "Dự án Mee"}, MID,
    )
    assert tpl["project"] == "Dự án Mee"
    assert len(tpl["items"]) == 1
    assert tpl["items"][0]["subject"] == "Deploy v1"
    assert tpl["items"][0]["due_date"] == "06/06/2026"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -k build_template -v`
Expected: FAIL — `AttributeError: ... has no attribute '_build_reconcile_template'`

- [ ] **Step 3: Implement in `meeting/graphs/chat_graph.py`** — first add the import near the top imports:

```python
from meeting.services import build_task_items, execute_tool, get_tool, list_tools
```

(replace the existing `from meeting.services import execute_tool, get_tool, list_tools` line).

Then add the builder (near `_reconcile_text`):

```python
async def _build_reconcile_template(
    session: AsyncSession,
    args: dict,
    meeting_ctx: dict,
    resolved_meeting_id: Optional[str],
) -> dict:
    """Build the reconcile template {project, items} for a create_task handoff.

    project defaults to the bound meeting's title (editable on the local card).
    items come from an explicit task in args, else the meeting's MoM action_items.
    """
    project = (meeting_ctx or {}).get("title") or ""
    explicit_title = args.get("title") or args.get("subject")
    if explicit_title:
        items = [{
            "subject": explicit_title,
            "assignee": args.get("assignee", ""),
            "due_date": args.get("deadline") or args.get("due_date", ""),
            "description": args.get("description", ""),
        }]
    elif resolved_meeting_id:
        action_items = await repo.get_mom_action_items(
            session, uuid.UUID(resolved_meeting_id)
        )
        items = build_task_items(action_items)
    else:
        items = []
    return {"project": project, "items": items}
```

- [ ] **Step 4: Run test to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -k build_template -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph.py tests/meeting/test_reconcile_bridge.py
git commit -m "feat(chat): build reconcile template from MoM action_items"
```

---

### Task 4: `pm_call` handles `kind="reconcile"`

**Files:**
- Modify: `meeting/graphs/chat_graph.py` (`make_pm_call` inner `pm_call`)
- Test: `tests/meeting/test_pm_graph_loop.py` (extend — reuses its `FakeClient`)

- [ ] **Step 1: Write the failing test** — append to `tests/meeting/test_pm_graph_loop.py`:

```python
async def test_pm_call_reconcile_sends_text_and_datapart():
    client = FakeClient([completed("Đã đối chiếu.")])
    graph = _build(client, MemorySaver())
    cfg = _config("t-reconcile")

    items = [{"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01"}]
    state = {
        "user_message": "đồng bộ task",
        "pm_next_payload": {
            "kind": "reconcile", "project": "GIP", "items": items,
            "text": "Đối chiếu ... trên dự án GIP:\n1. viết migration",
        },
    }
    result = await graph.ainvoke(state, cfg)

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã đối chiếu."
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert "GIP" in sent["text"]
    assert sent["data_part"]["kind"] == "reconcile_items"
    assert sent["data_part"]["project"] == "GIP"
    assert sent["data_part"]["items"] == items
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_pm_graph_loop.py::test_pm_call_reconcile_sends_text_and_datapart -v`
Expected: FAIL — `data_part` is `None` (the `else` branch sends no DataPart), so `sent["data_part"]["kind"]` raises `TypeError: 'NoneType' object is not subscriptable`.

- [ ] **Step 3: Implement** — in `make_pm_call`'s `pm_call`, find the `try:` block that branches on `kind`. Add a `reconcile` branch BEFORE the `if kind == "approval":` branch:

```python
            if kind == "reconcile":
                data_part = {
                    "kind": "reconcile_items",
                    "project": payload.get("project", ""),
                    "items": payload.get("items", []),
                }
                result = await client.send_message(
                    payload.get("text", ""),
                    task_id=task_id, context_id=context_id, data_part=data_part,
                )
            elif kind == "approval":
```

(Change the existing `if kind == "approval":` to `elif kind == "approval":`. Leave the `else:` start/text branch unchanged.)

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_pm_graph_loop.py -v`
Expected: PASS (new test + all existing pm-loop tests).

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph.py tests/meeting/test_pm_graph_loop.py
git commit -m "feat(chat): pm_call sends reconcile text + items DataPart"
```

---

### Task 5: `route_after_agent_execute` — bridge router

**Files:**
- Modify: `meeting/graphs/chat_graph.py`
- Test: `tests/meeting/test_reconcile_bridge.py` (extend)

- [ ] **Step 1: Write the failing test** — append to `tests/meeting/test_reconcile_bridge.py`:

```python
def test_route_after_agent_execute_reconcile_goes_to_pm():
    assert chat_graph.route_after_agent_execute({"agent_route": "reconcile"}) == "pm_call"


def test_route_after_agent_execute_default_goes_to_agent():
    assert chat_graph.route_after_agent_execute({"agent_route": "agent"}) == "agent"
    assert chat_graph.route_after_agent_execute({}) == "agent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -k route_after_agent_execute -v`
Expected: FAIL — `AttributeError: ... has no attribute 'route_after_agent_execute'`

- [ ] **Step 3: Implement in `meeting/graphs/chat_graph.py`** (next to `route_after_agent_tools`):

```python
def route_after_agent_execute(state: ChatState) -> Literal["agent", "pm_call"]:
    """After an approved create_task, bridge into the pm reconcile loop;
    otherwise loop back to the agent (normal side-effect tools)."""
    return "pm_call" if state.get("agent_route") == "reconcile" else "agent"
```

- [ ] **Step 4: Run test to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -k route_after_agent_execute -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph.py tests/meeting/test_reconcile_bridge.py
git commit -m "feat(chat): router for agent_execute → pm reconcile bridge"
```

---

### Task 6: `agent_tools` builds the template for create_task; `agent_execute` bridges

This wires the two nodes; the full behavior is verified by the graph test in Task 7. Here we make the node edits and keep the suite green.

**Files:**
- Modify: `meeting/graphs/chat_graph.py` (`make_agent_tools`, `make_agent_execute`, builder edge)

- [ ] **Step 1: Edit `make_agent_tools`** — in the `for tc in tool_calls:` loop, replace the side-effect deferral block:

```python
            if spec and spec.get("side_effect"):
                if pending is None:
                    # Defer to HITL; agent_execute appends this tool's result.
                    pending = {"id": tc["id"], "name": name, "args": args}
                else:
```

with (build the reconcile template for create_task BEFORE the GATE-1 interrupt so the card shows {project, items} with an editable project):

```python
            if spec and spec.get("side_effect"):
                if pending is None:
                    if name == "create_task":
                        template = await _build_reconcile_template(
                            session, args, state.get("meeting_context") or {}, resolved
                        )
                        pending = {"id": tc["id"], "name": name, "args": template}
                    else:
                        pending = {"id": tc["id"], "name": name, "args": args}
                else:
```

- [ ] **Step 2: Edit `make_agent_execute`** — replace the `if action == "approved":` block (through the function's `return`) so an approved create_task bridges to pm. The full replacement:

```python
        # Approved create_task → bridge into the pm reconcile loop (GATE 2 is
        # pm-agent's own write approval). The user may edit `project` on the card.
        if action == "approved" and name == "create_task":
            template = dict(args)  # {project, items}
            if decision.get("edited_args"):
                template.update(decision["edited_args"])
            project = template.get("project", "")
            items = template.get("items", [])
            logger.info(
                "[Node agent_execute] create_task → pm reconcile (%d item(s))", len(items)
            )
            return {
                "pending_tool": None,
                "user_decision": None,
                "agent_route": "reconcile",
                "pm_next_payload": {
                    "kind": "reconcile", "project": project, "items": items,
                    "text": _reconcile_text(project, items),
                },
                "pm_rounds": 0,
                "tool_result": {
                    "status": "reconcile_handoff", "project": project, "count": len(items),
                },
            }

        if action == "approved":
            if decision.get("edited_args"):
                args = _inject_meeting(
                    decision["edited_args"], name, state.get("resolved_meeting_id")
                )
            result = await execute_tool(name, args, session=session, user_id=user_id)
        else:
            result = {"status": "rejected", "reason": decision.get("reason", "user rejected")}

        if tc_id is not None:
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": _json(result)})
        logger.info(f"[Node agent_execute] tool={name!r} action={action!r}")
        return {
            "agent_messages": messages,
            "pending_tool": None,
            "user_decision": None,
            "tool_result": result,
            "agent_route": "agent",
        }
```

- [ ] **Step 3: Edit `build_chat_graph`** — replace the plain edge:

```python
    g.add_edge("agent_execute", "agent")
```

with a conditional edge:

```python
    g.add_conditional_edges(
        "agent_execute",
        route_after_agent_execute,
        {"agent": "agent", "pm_call": "pm_call"},
    )
```

- [ ] **Step 4: Run the full suite (expect two known failures)**

Run: `venv/bin/python -m pytest tests/meeting -q`
Expected: the two `test_agent_loop.py` create_task side-effect tests FAIL (approved create_task no longer calls `execute_tool` — and their local `_build` graph has no `pm_call` node). This is the RED for Task 7; everything else PASSES. Do Task 7 next immediately.

- [ ] **Step 5: Commit**

```bash
git add meeting/graphs/chat_graph.py
git commit -m "feat(chat): bridge approved create_task into pm reconcile loop"
```

---

### Task 7: Full bridge graph test + fix the create_task agent-loop tests

The create_task side-effect examples in `test_agent_loop.py` now bridge to pm instead of executing locally. Switch them to `send_email` (a side-effect tool that still executes locally), and add the end-to-end bridge test.

**Files:**
- Modify: `tests/meeting/test_agent_loop.py`
- Test: `tests/meeting/test_reconcile_bridge.py` (extend — full graph)

- [ ] **Step 1: Fix `test_agent_loop.py`** — add a `SEND_SPEC` and switch the two create_task tests to `send_email`. In the specs block:

```python
SEND_SPEC = {
    "name": "send_email", "description": "send email", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "to": {"type": "string"}}},
}
_SPECS = {s["name"]: s for s in (RETRIEVE_SPEC, CREATE_SPEC, SWITCH_SPEC, SEND_SPEC)}
```

Rewrite `test_agent_side_effect_interrupts_then_executes` to use `send_email`:

```python
async def test_agent_side_effect_interrupts_then_executes(monkeypatch):
    ft = _install(monkeypatch, {"send_email": {"status": "sent_mock"}})
    llm = FakeLLM([
        tool([{"id": "c1", "name": "send_email", "arguments": '{"to": "a@x.vn"}'}]),
        text("Đã gửi email."),
    ])
    graph = _build(llm, MemorySaver())
    cfg = _config("side-effect")

    await graph.ainvoke(_initial("gửi email"), cfg)

    assert await _interrupted(graph, cfg)
    pending = await _interrupt_value(graph, cfg)
    assert pending["tool"] == "send_email"
    assert pending["args"]["to"] == "a@x.vn"
    assert pending["args"]["meeting_id"] == "bound-mid"
    assert ft.calls == []  # not executed before approval

    result = await graph.ainvoke(Command(resume={"action": "approved"}), cfg)

    assert not await _interrupted(graph, cfg)
    assert result["final_reply"] == "Đã gửi email."
    assert len(ft.calls) == 1
    assert ft.calls[0]["name"] == "send_email"
    assert len(llm.calls) == 2
```

Rewrite `test_agent_side_effect_rejected` to use `send_email` (keep its reject assertions):

```python
async def test_agent_side_effect_rejected(monkeypatch):
    ft = _install(monkeypatch)
    llm = FakeLLM([
        tool([{"id": "c1", "name": "send_email", "arguments": '{"to": "a@x.vn"}'}]),
        text("OK, mình không gửi nữa."),
    ])
    graph = _build(llm, MemorySaver())
    cfg = _config("rejected")

    await graph.ainvoke(_initial("gửi email"), cfg)
    assert await _interrupted(graph, cfg)

    result = await graph.ainvoke(Command(resume={"action": "rejected", "reason": "thôi"}), cfg)

    assert not await _interrupted(graph, cfg)
    assert ft.calls == []  # never executed
    assert result["final_reply"] == "OK, mình không gửi nữa."
    assert len(llm.calls) == 2
```

WHY `send_email` not `create_task`: the local `_build` graph in `test_agent_loop.py` keeps `g.add_edge("agent_execute", "agent")` (no `pm_call` node), and an approved create_task now sets `agent_route="reconcile"`. Only the full graph (Task 7 bridge test below) wires `pm_call`. Leave `test_agent_loop.py`'s `_build` unchanged.

- [ ] **Step 2: Run the edited tests to confirm they pass**

Run: `venv/bin/python -m pytest tests/meeting/test_agent_loop.py -v`
Expected: PASS (all).

- [ ] **Step 3: Write the failing bridge test** — append to `tests/meeting/test_reconcile_bridge.py`:

```python
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from meeting.graphs.chat_graph import (
    ChatState,
    agent_approve,
    make_agent,
    make_agent_execute,
    make_agent_tools,
    make_pm_call,
    pm_await,
    pm_reply,
    route_after_agent,
    route_after_agent_execute,
    route_after_agent_tools,
    route_after_pm_call,
)
from meeting.services.pm_agent_client import PmAgentResult

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SESSION = object()


def _resp_text(content):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def _resp_tool(calls):
    tcs = [SimpleNamespace(id=c["id"], type="function",
                           function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
           for c in calls]
    msg = SimpleNamespace(content=None, tool_calls=tcs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")])


class _FakeLLM:
    def __init__(self, scripted):
        self._s = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        i = len(self.calls); self.calls.append(kw); return self._s[i]


class _FakePm:
    def __init__(self, results):
        self._r = list(results); self.calls = []

    async def send_message(self, text, *, task_id=None, context_id=None, data_part=None):
        self.calls.append({"text": text, "task_id": task_id,
                           "context_id": context_id, "data_part": data_part})
        return self._r[len(self.calls) - 1]

    async def cancel(self, task_id):
        pass


CREATE_SPEC_B = {
    "name": "create_task", "description": "make tasks", "side_effect": True,
    "schema": {"type": "object",
               "properties": {"meeting_id": {"type": "string"}, "title": {"type": "string"}}},
}


class _FakeExec:
    def __init__(self): self.calls = []
    async def __call__(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args}); return {"status": "ok"}


def _build_full(llm, pm_client, checkpointer):
    g = StateGraph(ChatState)
    g.add_node("agent", make_agent(llm))
    g.add_node("agent_tools", make_agent_tools(SESSION))
    g.add_node("agent_approve", agent_approve)
    g.add_node("agent_execute", make_agent_execute(SESSION))
    g.add_node("pm_call", make_pm_call(pm_client))
    g.add_node("pm_await", pm_await)
    g.add_node("pm_reply", pm_reply)
    g.add_node("save_reply", lambda s: {})
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route_after_agent,
                            {"agent_tools": "agent_tools", "save_reply": "save_reply"})
    g.add_conditional_edges("agent_tools", route_after_agent_tools,
                            {"agent": "agent", "agent_approve": "agent_approve"})
    g.add_edge("agent_approve", "agent_execute")
    g.add_conditional_edges("agent_execute", route_after_agent_execute,
                            {"agent": "agent", "pm_call": "pm_call"})
    g.add_conditional_edges("pm_call", route_after_pm_call,
                            {"pm_await": "pm_await", "pm_reply": "pm_reply", "save_reply": "save_reply"})
    g.add_edge("pm_await", "pm_call")
    g.add_edge("pm_reply", "save_reply")
    g.add_edge("save_reply", END)
    return g.compile(checkpointer=checkpointer)


def _initial_b(msg):
    return {"session_id": "s", "user_id": str(UID), "user_message": msg,
            "resolved_meeting_id": "bound-mid",
            "meeting_context": {"id": "bound-mid", "title": "AI Innovation Project"}}


async def _interrupted_b(graph, cfg):
    snap = await graph.aget_state(cfg); return bool(snap.next)


async def _interrupt_val_b(graph, cfg):
    snap = await graph.aget_state(cfg)
    for t in snap.tasks:
        if t.interrupts:
            return t.interrupts[0].value
    return None


async def test_full_bridge_create_task_to_reconcile(monkeypatch):
    monkeypatch.setattr(chat_graph, "list_tools", lambda: [CREATE_SPEC_B])
    monkeypatch.setattr(chat_graph, "get_tool", lambda n: CREATE_SPEC_B if n == "create_task" else None)
    monkeypatch.setattr(chat_graph, "execute_tool", _FakeExec())

    async def fake_items(session, mid):
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([
        _resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}]),
    ])
    pm = _FakePm([
        PmAgentResult("task-1", "input_required", "Xác nhận tạo issue?", True,
                      [{"actions": "CREATE", "subject": "viết migration"}], context_id="ctx-1"),
        PmAgentResult("task-1", "completed", "Đã tạo issue trên Redmine.", False, None, context_id="ctx-1"),
    ])
    graph = _build_full(llm, pm, MemorySaver())
    cfg = {"configurable": {"thread_id": "bridge"}}

    # Turn 1: agent calls create_task → GATE 1 (local) interrupt
    await graph.ainvoke(_initial_b("đồng bộ các việc trong họp lên Redmine"), cfg)
    assert await _interrupted_b(graph, cfg)
    gate1 = await _interrupt_val_b(graph, cfg)
    assert gate1["tool"] == "create_task"
    assert gate1["args"]["project"] == "AI Innovation Project"   # default from title
    assert gate1["args"]["items"][0]["subject"] == "viết migration"
    assert pm.calls == []   # nothing sent to pm before GATE 1 approval

    # Approve GATE 1 with an edited project → bridges to pm → GATE 2 (pm need_approval)
    await graph.ainvoke(
        Command(resume={"action": "approved", "edited_args": {"project": "GIP"}}), cfg
    )
    assert await _interrupted_b(graph, cfg)
    gate2 = await _interrupt_val_b(graph, cfg)
    assert gate2["kind"] == "need_approval"
    assert len(pm.calls) == 1
    assert "GIP" in pm.calls[0]["text"]                          # edited project used
    assert pm.calls[0]["data_part"]["project"] == "GIP"
    assert pm.calls[0]["data_part"]["items"][0]["subject"] == "viết migration"

    # Approve GATE 2 → pm completes
    result = await graph.ainvoke(Command(resume={"approval_action": "approve"}), cfg)
    assert not await _interrupted_b(graph, cfg)
    assert result["final_reply"] == "Đã tạo issue trên Redmine."
    assert len(pm.calls) == 2


async def test_bridge_reject_gate1_no_handoff(monkeypatch):
    monkeypatch.setattr(chat_graph, "list_tools", lambda: [CREATE_SPEC_B])
    monkeypatch.setattr(chat_graph, "get_tool", lambda n: CREATE_SPEC_B if n == "create_task" else None)
    monkeypatch.setattr(chat_graph, "execute_tool", _FakeExec())

    async def fake_items(session, mid):
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    llm = _FakeLLM([
        _resp_tool([{"id": "c1", "name": "create_task", "arguments": "{}"}]),
        _resp_text("OK, mình không đồng bộ nữa."),
    ])
    pm = _FakePm([])
    graph = _build_full(llm, pm, MemorySaver())
    cfg = {"configurable": {"thread_id": "bridge-reject"}}

    await graph.ainvoke(_initial_b("đồng bộ lên Redmine"), cfg)
    assert await _interrupted_b(graph, cfg)

    result = await graph.ainvoke(
        Command(resume={"action": "rejected", "reason": "thôi"}), cfg
    )
    assert not await _interrupted_b(graph, cfg)
    assert pm.calls == []                       # never bridged to pm
    assert result["final_reply"] == "OK, mình không đồng bộ nữa."
```

- [ ] **Step 4: Run the bridge tests to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -v`
Expected: PASS (all reconcile-bridge tests).

- [ ] **Step 5: Run the FULL suite**

Run: `venv/bin/python -m pytest tests/meeting -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add tests/meeting/test_reconcile_bridge.py tests/meeting/test_agent_loop.py
git commit -m "test(chat): end-to-end create_task → reconcile bridge"
```

---

### Task 8: `classify_intent` — bias meeting-derived tasks to the agent

**Files:**
- Modify: `meeting/graphs/chat_graph.py` (`classify_intent` system prompt)
- Test: `tests/meeting/test_reconcile_bridge.py` (extend — prompt assertion only; classify itself calls a live LLM and is not unit-tested)

- [ ] **Step 1: Write the failing test** — append to `tests/meeting/test_reconcile_bridge.py`:

```python
def test_classify_prompt_routes_meeting_tasks_to_agent():
    import inspect
    src = inspect.getsource(chat_graph.classify_intent)
    assert "biên bản" in src and "agent" in src
    assert "đồng bộ" in src or "lên Redmine" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -k classify_prompt -v`
Expected: FAIL — the example/line is not yet in the prompt source.

- [ ] **Step 3: Implement** — in `classify_intent`'s `system_prompt`, add a bullet under the `"agent"` section. Insert into the `"agent"` description block (after the "tạo task nội bộ..." line):

```python
        "  • đồng bộ / tạo task TỪ biên bản họp lên Redmine — agent tự dựng danh "
        "sách việc từ MoM rồi chuyển cho pm-agent đối chiếu (KHÔNG tự route sang pm_task)\n"
```

And add to the Examples list (after the existing `"tạo task cho Mai..."` example):

```python
        '  "đồng bộ các việc trong biên bản họp lên Redmine" → agent\n'
        '  "tạo issue trên Redmine cho từng action item của cuộc họp" → agent\n'
```

- [ ] **Step 4: Run test to verify pass**

Run: `venv/bin/python -m pytest tests/meeting/test_reconcile_bridge.py -k classify_prompt -v`
Expected: PASS

- [ ] **Step 5: Run the FULL suite**

Run: `venv/bin/python -m pytest tests/meeting -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add meeting/graphs/chat_graph.py tests/meeting/test_reconcile_bridge.py
git commit -m "feat(chat): classify meeting-derived task sync to the agent branch"
```

---

## Self-Review

**Spec coverage:**
- Goal #1 (create_task bridges into pm loop) → Tasks 5, 6 (router + node edits + edge).
- Goal #2 (two HITL gates) → Task 6 (GATE 1 local approval preserved; bridge to pm GATE 2) + Task 7 (verifies both interrupts chain).
- Goal #3 (project pre-filled from title, editable) → Task 3 (default = title) + Task 6 (`edited_args` merge) + Task 7 (asserts edited "GIP" used).
- Reconcile message + DataPart → Tasks 2, 4.
- classify tweak → Task 8.
- Template from MoM action_items → Tasks 1, 3.

**Placeholder scan:** none — every code step has complete code.

**Type/name consistency:** `_reconcile_text(project, items)` (Task 2) used in Task 6; `_build_reconcile_template(session, args, meeting_ctx, resolved)` (Task 3) used in Task 6; `route_after_agent_execute` (Task 5) used in Task 6 edge + Task 7 harness; `build_task_items` (Task 1) used in Task 3; `pm_next_payload` `kind="reconcile"` shape consistent across Tasks 4, 6, 7; `reconcile_items` DataPart kind consistent in Tasks 4, 7.

**Known interaction:** Task 6 deliberately breaks two `test_agent_loop.py` tests (approved create_task no longer executes locally); Task 7 fixes them by switching to `send_email`. Run order matters — do Task 7 immediately after Task 6 (the plan notes this).
