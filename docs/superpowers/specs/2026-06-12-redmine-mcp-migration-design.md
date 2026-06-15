# Redmine via MCP — design (P2)

**Branch:** `feat/personalized-user-prompt`
**Date:** 2026-06-12
**Status:** Approved (design); implementation pending.

## Goal

Let Mee's chat agent talk to Redmine **directly through the deployed MCP server**
(`MCP_REDMINE_URL=https://mcp-redmine.vngcloud.vn/mcp`), instead of routing all PM
work through the **pm-agent A2A** integration. pm-agent is **demoted to opt-in** —
entered only when the user explicitly asks for it — rather than being the default
path for issue/ticket operations.

## Decisions (locked)

1. **Credentials:** Mee uses its own single `REDMINE_API_KEY` from env as the
   Bearer token (single-tenant / service account). No per-user auth.
2. **Server:** adopt the already-deployed, already-curated MCP server (5 tools).
   Mee adds an MCP **client** bridge; it does not build or run a server.
3. **Batch reconcile:** the **LLM reconciles over MCP** — it reads existing issues
   via `list_redmine_issue`, decides create-vs-update per item, and execution is a
   deterministic apply loop. pm-agent is fully demoted to opt-in.
4. **Tool registration:** hardcoded specs for the 5 known tools (not dynamic
   `list_tools()` discovery at import).
5. **Batch HITL:** one approval card for the whole batch (not one per issue).
6. **create_task:** gains an optional per-item `issue_id` so the LLM can mark
   updates; absent `issue_id` ⇒ create.
7. **pm-agent:** branch + nodes stay wired and tested; entered only on explicit
   "pm-agent" mention in the user message.

## Reference sources (read during design)

- `projects/pm-agent/src/mcp_server/mcp_http_client.py` — proven streamable-http
  client pattern to port (simplified).
- `projects/mcp-redmine/README.md` + `redmine_mcp_server.py` — the deployed
  server's tool surface and auth (Bearer token = Redmine API key, validated
  against `/users/current.json`).

## The deployed MCP server (target surface)

FastMCP streamable-http at `…/mcp`. Auth: `Authorization: Bearer <REDMINE_API_KEY>`.
Exactly 5 tools:

| Tool | Kind | Required args | Optional args |
|------|------|---------------|---------------|
| `get_redmine_projects` | read | — | — |
| `list_redmine_issue` | read | `project_name` | `assigned_to`, `status` |
| `create_redmine_issue` | **write** | `project_name`, `subject`, `tracker`, `assigned_to` | `status`, `priority`, `description`, `target_version`, `category` |
| `update_redmine_issue` | **write** | `issue_id`, `project_name` | `subject`, `description`, `tracker`, `status`, `priority`, `assigned_to`, `notes`, `target_version`, `category` |
| `create_redmine_subtask` | **write** | `parent_issue_id`, `project_name`, `subject`, `assigned_to` | `tracker`, `status`, `priority`, `description`, `target_version`, `category` |

## Components

### 1. `meeting/services/redmine_mcp_client.py` (new)

Simplified port of pm-agent's `MCPHTTPClient`. Per-call streamable-http session:

```
streamablehttp_client(MCP_REDMINE_URL, headers={"Authorization": f"Bearer {REDMINE_API_KEY}"})
  → ClientSession → initialize → call_tool(name, args)
```

- Result parsing ported verbatim from pm-agent `_do_call_tool`:
  `structuredContent` first (unwrap sole-key `{"result": …}`), `isError` → `{"error": …}`,
  text → JSON parse, else `{"message": text}`.
- `get_redmine_mcp_client()` — lazy env singleton (mirrors `get_pm_agent_client`).
- **Dropped from pm-agent's version:** AgentBase provider auth, `@requires_api_key`,
  `on_receive_auth` callback, per-user keys, LangChain `StructuredTool` conversion,
  disk tool-schema cache. Mee has one key, so schemas are hardcoded in the registry
  (component 2) and there is no discovery step.
- Env: `MCP_REDMINE_URL`, `REDMINE_API_KEY` (both already in `.env`).

### 2. `meeting/services/tools/redmine.py` (new)

Registers the 5 tools into the existing `TOOLS` registry via the local `@tool`
decorator, with **hardcoded** schemas matching the table above. Each executor is a
thin proxy:

```python
async def _exec(args, *, session, user_id):
    return await get_redmine_mcp_client().call_tool("<mcp_name>", args)
```

- Reads (`list_redmine_issue`, `get_redmine_projects`): `side_effect=False`.
- Writes (`create_redmine_issue`, `update_redmine_issue`, `create_redmine_subtask`):
  `side_effect=True`.
- These tools take `project_name`/`issue_id`, **not** `meeting_id`, so
  `_inject_meeting` leaves them alone.
- Registered in `meeting/services/tools/__init__.py` after the existing imports
  (registration order = LLM tool-offer order).

The existing `side_effect` HITL machinery handles **single writes** with no graph
change: agent calls `create_redmine_issue` → `agent_tools` defers it as
`pending_tool` → `agent_approve` interrupt (card) → `agent_execute` runs it via
`execute_tool`.

### 3. Batch sync rewrite — `agent_execute` (in `chat_graph/agent.py`)

`create_task` remains the meeting-aware batch **planner**
(`_build_reconcile_template` builds `{project, items}` from MoM; the HITL card is
unchanged). The change is the approved-`create_task` branch of `agent_execute`:

- **Remove** the `_reconcile_payloads → pm_call` bridge (route `"reconcile"`).
- **Add** an MCP apply loop: for each item in the approved (possibly edited)
  template, call `update_redmine_issue` when the item carries an `issue_id`, else
  `create_redmine_issue`, through the MCP client (the tools bundle, so tests can
  inject a fake). Collect per-item `{ok|error, …}` into a summary `tool_result`,
  set `final_reply` to a Vietnamese summary, route to `save_reply` (or back to
  `agent` to let it phrase the summary — see Open Items).
- `_build_reconcile_template` items gain an optional `issue_id` passthrough; the
  LLM is prompted (component 5) to call `list_redmine_issue` first and stamp
  `issue_id` on items it intends to update. Absent ⇒ create.
- `route_after_agent_execute` loses the `"reconcile"` → `pm_call` case (or keeps it
  guarded for the explicit opt-in only; pm-agent batch reconcile is no longer the
  create_task default).

**One approval card for the whole batch** (today's GATE 1), then deterministic
apply. No GATE 2 / pm-agent in the default path.

### 4. Routing — `classify_intent` (`_chat_prompts.py::CLASSIFY_SYSTEM_PROMPT`)

`pm_task` demoted to opt-in:
- Redmine issue ops (list / create / update / sync, project-scoped issue queries,
  overdue/workload) now classify as **`agent`** — the agent owns the MCP tools.
- `pm_task` fires **only** when the user explicitly names pm-agent (e.g. "dùng
  pm-agent", "reconcile bằng pm-agent").
- Update the few-shot examples to reflect the new mapping.
- The `pm_call`/`pm_await`/`pm_error` branch and `pm.py` stay wired + tested; just
  rarely entered. `route_entry` unchanged.

### 5. Agent system prompt (`_chat_prompts.py::_agent_system_prompt`)

Add Redmine guidance:
- For "list issues / overdue / workload in project X" → `list_redmine_issue`.
- For one explicit issue → `create_redmine_issue` / `update_redmine_issue`
  (needs approval).
- For batch MoM→Redmine sync → call `list_redmine_issue` first to find existing
  issues, then `create_task` with items (stamp `issue_id` on items to update).
- Disambiguate `create_task` (batch from a meeting's MoM, multi-item, meeting-aware)
  vs `create_redmine_issue` (one issue the user dictated).
- `assigned_to` / `tracker` / `project_name` are Redmine-native fields.

### 6. HITL / replay-safety

Invariant preserved: `agent_approve` is the only interrupt and performs no side
effect; all MCP writes happen post-approval in `agent_execute`, so each runs
exactly once across resume/replay. The batch apply performs N writes in one node →
**partial-failure risk** on a mid-batch crash (same risk pm-agent's reconcile had).
v1 collects per-item results and reports failures; no transactional rollback.

### 7. Config / deps

- `requirements.txt` += `mcp>=1.25.0` (matches pm-agent's pin; client only).
- `.env.example` += `MCP_REDMINE_URL`, `REDMINE_API_KEY` with a caveat note that the
  real values live in `.env`.
- No DB / Alembic changes.

## Testing

- **Client parsing** (`test_redmine_mcp_client.py`): `structuredContent` unwrap,
  `isError`, text→JSON, `{"message": …}` fallback — against a fake MCP
  session/transport (no network).
- **Registry**: the 5 tools register with the correct `side_effect` flags and
  schemas; reads vs writes.
- **Batch apply** (`agent_execute`): approved `create_task` with items → calls MCP
  `create`/`update` appropriately (inject a fake tools bundle / fake MCP client);
  per-item result aggregation; partial-failure reporting.
- **classify**: Redmine phrasings now → `agent`; explicit "pm-agent" → `pm_task`.
- Reuse the existing `tools=` / `pm_client=` / `agent_llm=` DI seams.

## Out of scope (keep intact)

- AgentBase memory grounding, `list_recordings` / `recording_mom` crawl chain, the
  Q1 staleness loop — orthogonal to this plan.
- pm-agent A2A branch — stays as the opt-in path.
- Per-user Redmine identities (Mee uses one service key in v1).

## Open items (resolve during implementation)

- After batch apply, whether to route to `save_reply` with a code-built summary, or
  loop back to `agent` to let the LLM phrase it (extra round, nicer prose). Lean
  `save_reply` for determinism.
- Exact trigger phrasing / few-shot examples for the explicit pm-agent opt-in.
- Whether `update_to_events` needs a new step label for MCP apply progress.
