# Role-Persona Proactive Kickoff — Design Spec

**Branch:** `feat/personalized-user-prompt` (the branch's headline feature)
**Status:** Design approved 2026-06-13. Spec for a fresh-session TDD build.
**Memory:** `role-persona-kickoff-feature`, `agentbase-memory-api-setup`, `redmine-mcp-migration-plan`, `db-alembic-drift-remote-ahead`.

## Goal

When a user opens a chat, **Mee speaks first** with a greeting tailored to the
user's **role**, grounded in that user's live data. Two motivating examples:

- **Applied AI Intern** → "Hi, I'm Mee — today your tasks are… As an Applied AI
  Intern I'd prioritize…" (own-task focus).
- **BA** → "Hi… there are X new tasks across Y projects you're on — want to
  review?" (cross-project overview).

## Decisions (locked in brainstorm 2026-06-13)

1. **Role pool storage** = **Postgres `roles` table** (authoritative, enumerable,
   editable). NOT AgentBase — AgentBase is insert-only / no-delete /
   similarity-recall (per `agentbase-memory-api-setup`), wrong shape for a catalog.
2. **User identity (v1)** = single `get_or_create_dev_user` with a **settable
   role**; real multi-user auth (Email/UID from Microsoft) is a deferred separate
   project.
3. **Persona storage** = AgentBase **`user_prefs/{actorId}`** (USER_PREFERENCES
   strategy) — holds the user's role. (This is the `mee-user-persona` store seen
   in traces.)
4. **Kickoff** = **LLM-generated, data-grounded** — one LLM call over
   `{role.description + role.kickoff_prompt + the user's live tasks/projects}`.
5. **UX** = **auto first agent message on chat-open** when the thread is empty.

## Architecture / components (each small + testable)

1. **`roles` table + repo** — schema `{id, name UNIQUE, description,
   kickoff_prompt, created_at}`; `repo.get_role(name)`, `repo.list_roles()`.
   Alembic migration + a seed (Applied AI Intern, BA) — see Migration note.
2. **Persona read** — extend `meeting/memory_client.py`:
   `get_user_role(actor_id) -> str | None` reading AgentBase
   `user_prefs/{actorId}` (mirror the existing `search_project_record`
   pattern: sync urllib in a thread, best-effort, returns None on miss/error).
3. **Role→data mapping** — pure function `role_data_plan(role_name) -> spec`
   choosing which Redmine MCP reads to run:
   - intern → own assigned tasks (`get_workload_by_assignee` / `list_redmine_issue`)
   - BA → cross-project new/unassigned (`list_redmine_issue` across projects /
     `get_unassigned_issues`)
   - default/unknown → minimal (no data) generic greeting.
   Reuses existing Redmine MCP read tools (see `redmine-mcp-migration-plan`).
4. **Kickoff builder** — `build_kickoff_messages(role, data) -> messages` (pure
   prompt assembly) + a single LLM call (reuse `_llm_client`/`_llm_model`, strip
   `<think>`). Returns greeting text. The LLM call is the only side-effect.
5. **Backend entry** — `POST /api/chat/sessions/{id}/kickoff` → resolves the
   session's user → `get_user_role` → `get_role` (pool) → fetch role data →
   `build_kickoff_messages` → LLM → greeting. **Persist** the greeting as an
   `agent` message in `chat_messages` so it survives refresh and lands in history.
   Returns `{reply}`.
6. **FE (`ChatPane`)** — on mount/session-open, if the thread is **empty** (no
   messages, no pending), call the kickoff endpoint once and render the returned
   greeting as the first agent bubble. Guard against double-fire (a ref/flag).
   Keep the WelcomeBanner only as the no-role / failure fallback.

## Data flow

```
open chat → ensureSession → thread empty?
  └─ yes → POST /sessions/{id}/kickoff
            → resolve user (dev user) → get_user_role(user_prefs/{actorId})
            → get_role(name) from roles table
            → role_data_plan → Redmine MCP reads (own vs cross-project)
            → build_kickoff_messages → LLM → greeting
            → persist as agent message → return {reply}
       → FE renders greeting as first agent message
```

## Error handling (never block chat)

- No persona / no role → skip kickoff, show today's generic WelcomeBanner.
- Role not in pool → default greeting (no data fetch).
- Redmine MCP unreachable → greeting from role text only, omit the data line.
- LLM failure → static per-role fallback string; never 500 the chat open.

## Testing (TDD; LLM + AgentBase + MCP mocked)

- `repo.get_role` / `list_roles` against a seeded test row.
- `get_user_role` — parses role from a fake `user_prefs` record; None on miss/error.
- `role_data_plan` — pure: correct read-set per role + default.
- `build_kickoff_messages` — pure: includes description + kickoff_prompt + data;
  shape stable.
- kickoff endpoint — happy path (greeting persisted + returned) and each
  fallback (no role, MCP down, LLM error) returns gracefully.

## Migration note (IMPORTANT — `db-alembic-drift-remote-ahead`)

The shared prod DB is stamped **past** the repo's Alembic head, and the backend
is run **without** `alembic upgrade head`. So the new `roles` migration must be
authored against the repo head, but **applying it to the shared DB needs an
explicit, careful step** (the table won't exist just by booting). Build task:
generate the migration, confirm the repo head lineage, and document/apply the
`roles` table creation to the shared DB out-of-band. Consider an idempotent
`CREATE TABLE IF NOT EXISTS` safety or a one-off apply script if the drift makes
`alembic upgrade` unsafe.

## v1 scope / YAGNI

- Single dev user; role read from `user_prefs/{actorId}` (settable — seed it via
  a small write or the existing memory write path).
- **2 roles seeded** (Applied AI Intern, BA) + a default fallback.
- Auto-kickoff on empty thread only (not on every message).
- **Deferred:** real multi-user identity/auth; a pool-admin UI; per-org role
  customization; richer per-role data templates.

## Open verification for the build (confirm in code, fresh session)

- Exact `memory_client` read shape for `user_prefs/{actorId}` (does a persona
  record exist / how is role encoded — a line in the record text? a key?).
- How `actorId` is derived for the dev user (the AgentBase actor used today).
- `ChatPane` mount/empty-thread hook point + the `api.chat` client method to add.
- Whether to fold kickoff into `create_session` vs a separate endpoint (spec
  assumes separate; revisit if it simplifies the FE).
