# chat_graph Phase 2 — DI seams + package split (approach B)

> Execute with `superpowers:executing-plans`, inline. Safety net = `venv/bin/python -m
> pytest tests/meeting -q` must stay **77 passed** after every task. Run with
> `ECC_GATEGUARD=off`. One commit per task.

**Goal:** finish the chat_graph reorg. Phase 1 extracted the seam-free helpers
(`_chat_state`/`_chat_llm`/`_chat_prompts`/`_chat_serde`). Phase 2 (this plan):
**(B) dependency-injection** for the test-patched seams so the monkeypatch-the-module-global
coupling is gone, then split `chat_graph.py` into a `chat_graph/` package with a facade.

**Branch:** `feat/backend-agents`.

---

## What actually breaks under a package split (the seam audit)

Tests reach into `chat_graph`'s namespace today. Two distinct mechanisms:

| Patched as | Examples | Survives a package split? |
|---|---|---|
| **Module attribute** `chat_graph.repo.X` | `find_meetings_by_title`, `get_mom_action_items` | **YES** — `repo` is the shared `meeting.db.repositories` module object; patching its attribute is global. Any submodule doing `from meeting.db import repositories as repo` and calling `repo.X()` sees it. **→ repo needs NO DI.** |
| **Name binding** `chat_graph.list_tools` / `get_tool` / `execute_tool` / `_llm_client` | `test_agent_loop`, `test_agent_recording_scope`, `test_reconcile_bridge`, `test_chat_routing` | **NO** — once the caller lives in a submodule with its own `from meeting.services import list_tools`, rebinding `chat_graph.list_tools` no longer reaches it. **→ these need DI.** |

So Phase 2's DI surface = **the tool functions** (`list_tools`/`get_tool`/`execute_tool`/
`build_task_items`, bundled) **+ the classify LLM**. `repo` stays as-is.

**The toolset bundle:** the real `meeting.services` module already exposes all four
(`list_tools`, `get_tool`, `execute_tool`, `build_task_items`) — so the default bundle IS
`meeting.services`, and a test fake is any object exposing those four attributes.

---

## Task 1 — DI the agent tool seams (still in `chat_graph.py`)

Production (`chat_graph.py`):
- Add module default: `import meeting.services as _services` (the default toolset).
- Helpers take a keyword-only `tools` with a default (so direct unit-test calls are unchanged):
  - `_openai_tools(*, tools=_services)` → `for s in tools.list_tools()`
  - `_inject_meeting(args, name, resolved, *, tools=_services)` → `tools.get_tool(name)`
  - `_build_reconcile_template(session, args, meeting_ctx, resolved, *, tools=_services)`
    → `tools.build_task_items(...)` (repo call unchanged)
- Factories gain keyword-only `tools=None`, resolve `ts = tools or _services`, and pass `tools=ts`
  to the helpers above:
  - `make_agent(llm=None, *, tools=None)`
  - `make_agent_tools(session, *, tools=None)`
  - `make_agent_execute(session, *, tools=None)`
  - **`agent_approve` → `make_agent_approve(*, tools=None)`** (was a bare node; now a factory so
    `get_tool` is injectable). `build_chat_graph` registers `make_agent_approve(tools=tools)`.
- `build_chat_graph(session, checkpointer, *, pm_client=None, agent_llm=None, tools=None)` threads
  `tools` into the four agent factories. `run_chat_turn`/`resume_chat_turn` are unchanged (they
  call `build_chat_graph(session, checkpointer)` → default services).

Tests — migrate off `monkeypatch.setattr(chat_graph, "list_tools"/"get_tool"/"execute_tool", …)`
onto an injected fake toolset. Add a shared helper per file:
```python
class FakeToolset:
    def __init__(self, specs, exec_results=None):
        self._specs = specs
        self.exec = FakeExec(exec_results)         # the existing fake callable
    def list_tools(self): return list(self._specs.values())
    def get_tool(self, n): return self._specs.get(n)
    async def execute_tool(self, name, args, *, session, user_id):
        return await self.exec(name, args, session=session, user_id=user_id)
    def build_task_items(self, items):
        from meeting.services import build_task_items as real
        return real(items)
```
- `test_agent_loop.py`: `_install(monkeypatch)` → `_toolset()` returning a `FakeToolset`; `_build`
  gains a `tools` arg passed to the factories (`make_agent(llm, tools=ts)`,
  `make_agent_tools(SESSION, tools=ts)`, `make_agent_approve(tools=ts)`,
  `make_agent_execute(SESSION, tools=ts)`); assert on `ts.exec.calls`.
- `test_agent_recording_scope.py`: same pattern (it imports `agent_approve` →
  switch to `make_agent_approve`).
- `test_reconcile_bridge.py`: `_build_full` threads `tools=ts`; the three `monkeypatch.setattr(
  chat_graph, …)` tool lines become a `FakeToolset`. The `chat_graph.repo.get_mom_action_items`
  patches **stay** (repo is fine). The direct `_build_reconcile_template(object(), …)` unit tests
  stay unchanged (default `tools=_services`, real `build_task_items`).
- Verify 77. Commit `refactor(chat): inject toolset into agent nodes (DI seam)`.

## Task 2 — DI the classify LLM seam

- `classify_intent` → **`make_classify_intent(llm=None)`** (closes over `llm or _llm_client()`).
  `build_chat_graph` registers `make_classify_intent(agent_llm)`.
- `route_entry` stays pure.
- `test_chat_routing.py`: replace `monkeypatch.setattr(chat_graph, "_llm_client", …)` +
  `await classify_intent({…})` with `await make_classify_intent(fake)({…})`.
- Verify 77. Commit `refactor(chat): inject LLM into classify_intent (DI seam)`.

**After Tasks 1–2:** no test patches a name binding in `chat_graph`'s namespace. The only
remaining `chat_graph.X` patch is `chat_graph.repo.*` (module attribute — split-safe).

## Task 3 — split `chat_graph.py` → `chat_graph/` package (pure motion + facade)

- `meeting/graphs/chat_graph.py` → `meeting/graphs/chat_graph/` with:
  - `__init__.py` — **facade**: re-export every public name the tests/consumers use (incl.
    `repo`, `ChatState`, `PM_MAX_ROUNDS`, `MAX_AGENT_ROUNDS`, `CLASSIFY_SYSTEM_PROMPT`, all
    `make_*`/`pm_*`/`route_*`/`agent_*`, `resolve_meeting`, `classify_intent` is gone — keep
    `make_classify_intent`; the pure re-exports `_reconcile_text`/`_decision_to_payload`/
    `_agent_system_prompt`/`_build_reconcile_template`/`_inject_meeting`/`_openai_tools`,
    `build_chat_graph`, `run_chat_turn`, `resume_chat_turn`, `_initial_turn_state`).
  - `context.py` — `make_load_context`, `make_save_reply`, `resolve_meeting`.
  - `classify.py` — `make_classify_intent`, `route_entry` (imports `CLASSIFY_SYSTEM_PROMPT`).
  - `agent.py` — agent factories + routers + `_openai_tools`/`_inject_meeting`/
    `_build_reconcile_template` + `_services` default.
  - `pm.py` — pm factories/nodes + routers.
  - `builder.py` — `build_chat_graph`.
  - `runner.py` — `run_chat_turn`/`resume_chat_turn`/`_initial_turn_state`/`_interrupt_or_complete`.
- Move the Phase-1 siblings into the package for cohesion: `_chat_state`→`_state.py`,
  `_chat_llm`→`_llm.py`, `_chat_prompts`→`_prompts.py`, `_chat_serde`→`_serde.py`; update imports;
  facade re-exports their public names. (Nothing outside chat_graph imports these directly.)
- Each submodule does `from meeting.db import repositories as repo` (keeps repo patching working);
  facade does the same so `chat_graph.repo` resolves.
- Check non-test importers: `grep -rn "from meeting.graphs.chat_graph\|graphs import chat_graph"`
  across `meeting/` (esp. `api/chat.py`, `app.py`) — the facade must cover them.
- Verify 77 + `venv/bin/python -c "import meeting.api.chat"` (or the app factory) imports clean.
  Commit `refactor(chat): split chat_graph into a package (facade preserves imports)`.

## Task 4 — regenerate `docs/diagrams/chat_graph.mmd`

- It's already dirty in the worktree and predates the `pm_error` retry node. Regenerate to match
  the current graph (classify → agent loop with approve/execute; pm_call ⇄ pm_await; pm_error
  retry edge back to pm_call; reconcile bridge edge agent_execute → pm_call).
- Commit `docs(chat): refresh chat_graph diagram for pm_error + reconcile bridge`.

---

## Self-review / risks
- **repo is deliberately NOT injected** — module-attribute patching is split-safe; DI-ing it would
  churn `resolve_meeting`/`load_context`/`save_reply` + `test_meeting_resolve` for zero gain (YAGNI).
- **Replay-safety unchanged** — DI only swaps *where* tool callables come from; the node topology
  (only `*_approve`/`pm_await`/`pm_error` interrupt; sends/execs are idempotent) is untouched.
- **Facade completeness** is the split's only real risk — driven by the test import lists +
  a grep of non-test importers. The 77-suite + an explicit `import meeting.api.chat` catch gaps.
- Tasks 1–2 are independently valuable (kill the coupling); Task 3 is the payoff; Task 4 is docs.
  Safe to stop after any committed task.
```
