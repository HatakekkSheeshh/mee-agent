# chat_graph.py reorganization — Implementation Plan

> **For agentic workers:** use `superpowers:executing-plans` (inline, TDD-by-regression). The
> existing suite is the safety net — `venv/bin/python -m pytest tests/meeting -q` must stay
> **77 passed** after every task (this is a pure refactor; no behavior changes, no new tests).

**Goal:** `meeting/graphs/chat_graph.py` (~870 lines) mixes six concerns. Split out the *pure,
non-test-patched* parts into sibling modules so the file shrinks and reads clearly, with **zero
behavior change** and **zero risk** to the existing tests.

**Branch:** `feat/backend-agents`. **Tech:** Python, LangGraph, pytest (asyncio auto-mode).

---

## The hard constraint (read first)

The tests **monkeypatch module globals on the `chat_graph` namespace**:
`chat_graph.repo`, `chat_graph.execute_tool`, `chat_graph.list_tools`, `chat_graph.get_tool`
(see `tests/meeting/test_reconcile_bridge.py`, `test_agent_loop.py`). For a patch to reach the
code, the code must resolve that name **through the `chat_graph` namespace at call time**.

⟹ **Anything that calls `repo` / `execute_tool` / `list_tools` / `get_tool` / `build_task_items`
MUST stay in `chat_graph.py`** (or, in Phase 2, use a call-time namespace lookup). Only
**pure, seam-free** helpers move in Phase 1.

Tests also import these names directly — they must remain importable as `chat_graph.X` (re-export
from the extracted modules covers this): `ChatState`, `PM_MAX_ROUNDS`, `MAX_AGENT_ROUNDS`,
`_reconcile_text`, `_build_reconcile_template`, `make_*`, `pm_*`, `route_*`, `classify_intent`,
`agent_approve`, `repo`, `list_tools`, `get_tool`, `execute_tool`.

---

## Phase 1 — extract pure helpers into sibling modules (LOW risk, do now)

Keep `chat_graph.py` as the import path (no package, no facade). It `from … import` the moved
names so every `chat_graph.X` reference still resolves.

### Task 1: `meeting/graphs/_chat_state.py`
Move: `ChatState` (TypedDict), `PM_MAX_ROUNDS`, `MAX_AGENT_ROUNDS`.
In `chat_graph.py`: `from meeting.graphs._chat_state import ChatState, PM_MAX_ROUNDS, MAX_AGENT_ROUNDS`.
- Run `pytest tests/meeting -q` → 77 passed. Commit `refactor(chat): extract ChatState + constants`.

### Task 2: `meeting/graphs/_chat_llm.py`
Move: `_llm_client`, `_llm_model`.
Re-import into `chat_graph.py`. (classify_intent / make_agent call them — they resolve via the
re-imported names; no test patches them, so a plain re-import is fine.)
- 77 passed. Commit `refactor(chat): extract LLM client helpers`.

### Task 3: `meeting/graphs/_chat_prompts.py`
Move: the `classify_intent` system-prompt **string** → `CLASSIFY_SYSTEM_PROMPT` constant;
`_agent_system_prompt(state)`; `_to_llm_messages(state, messages)`.
In `classify_intent`, reference `CLASSIFY_SYSTEM_PROMPT`. Re-import `_agent_system_prompt`,
`_to_llm_messages`.
- 77 passed. Commit `refactor(chat): extract prompts`.

### Task 4: `meeting/graphs/_chat_serde.py`
Move the **pure** (no repo/tool/LLM) serialization + payload helpers:
`_json`, `_tc_to_dict`, `_parse_tool_args`, `_seed_agent_messages`, `_last_assistant_text`,
`_reconcile_text`, `_result_to_dict`, `_decision_to_payload`.
Re-import all into `chat_graph.py` (test calls `chat_graph._reconcile_text`, so it must re-export).
⚠️ Do **NOT** move `_inject_meeting` (uses `get_tool`), `_openai_tools` (uses `list_tools`), or
`_build_reconcile_template` (uses `repo` + `build_task_items`) — they touch patched seams.
- 77 passed. Commit `refactor(chat): extract pure serde/payload helpers`.

**After Phase 1:** `chat_graph.py` drops ~250–300 lines and holds only: re-imports, the
seam-touching helpers (`_openai_tools`, `_inject_meeting`, `_build_reconcile_template`), all node
factories, routers, `build_chat_graph`, and the runner. Behavior identical.

---

## Phase 2 — split nodes into a package (HIGHER risk, separate session)

Convert `chat_graph.py` → `chat_graph/` package; `__init__.py` is a **facade** re-exporting every
name above. Submodules: `agent.py` (agent nodes + routers + `_openai_tools`/`_inject_meeting`/
`_build_reconcile_template`), `pm.py` (pm nodes + routers + payloads), `classify.py`, `context.py`
(`make_load_context`/`make_save_reply`/`resolve_meeting`), `builder.py`, `runner.py`.

**The seam problem:** agent/pm/context submodules call `repo`/`execute_tool`/`list_tools`/`get_tool`.
Two acceptable resolutions (pick one, be consistent):
- **(A) Call-time namespace lookup** — `from meeting.graphs import chat_graph as _cg` (lazy, inside
  the function) then `_cg.execute_tool(...)`. Keeps existing tests unchanged. Slightly ugly.
- **(B) Dependency injection (preferred long-term)** — pass the tool registry + repo into the node
  factories (`make_agent_tools(session, *, tools=…)`), and **migrate the tests** from
  "patch the module global" to the DI seams (they already inject `llm`/`pm_client`). Cleaner,
  but touches the 3 test files.

Recommendation: do Phase 2 with **(B)** — it removes the monkeypatch coupling permanently and makes
further splits trivial. Budget it as its own plan; verify 77 passed throughout.

Also worth doing in Phase 2: regenerate `docs/diagrams/chat_graph.mmd` (stale — predates the
`pm_error` retry node).

---

## Self-review
- **No behavior change** in Phase 1 — only code motion + re-imports; the suite is the oracle.
- **Seam safety:** Phase 1 explicitly excludes the 5 patched seams; Phase 2 names the trick.
- **Import stability:** every `chat_graph.X` the tests/consumers use is re-exported.
