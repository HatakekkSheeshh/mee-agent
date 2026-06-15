# OID → Position → Role Persona — Design Spec

**Branch:** `feat/personalized-user-prompt`
**Status:** Design approved 2026-06-14. Spec for a fresh-session TDD build.
**Extends:** `2026-06-13-role-persona-kickoff-design.md` (the kickoff feature, built + committed `ee407a0`) and the plan `2026-06-14-oid-role-persona-plan.md`.
**Memory:** `role-persona-kickoff-feature`, `agentbase-memory-api-setup`, `db-alembic-drift-remote-ahead`.

## Goal

Replace the `VITE_KICKOFF_ROLE` dev stopgap. At O365 login, fetch the user's
`jobTitle` from Microsoft Graph `/me`, map it to a canonical `roles.name`,
persist it on the user row, and make the kickoff use the **logged-in user's real
role** — per-user, derived from their O365 identity.

## Scope decision (locked in brainstorm 2026-06-14)

This work is **Feature 1** of a two-feature personalization story:

- **Feature 1 (this spec): identity → role → kickoff.** Graph `/me` → `jobTitle`
  → `users.role_id` → kickoff reads the real role. Ready to build.
- **Feature 2 (deferred, its own brainstorm/spec): learned style persona.** The
  agent infers each user's tone/style from their chat history, distills it into
  `mee-user-persona` (`user_prefs/{OID}`), and injects it into the chat agent's
  system prompt (`_agent_system_prompt`) for personalized replies. This is the
  feature that makes AgentBase `user_prefs` a real consumer.

The two share exactly one piece of plumbing — **per-user OID keying** — which
Feature 1 establishes via the `users` row (already keyed by OID). Feature 2
builds on it later.

## Decisions (locked)

1. **Role storage = `users.role_id` FK → `roles.id`** (NOT AgentBase `user_prefs`).
   The `users` table (post-O365-merge) is already keyed by OID; the role is a
   property of the user resolved at login, so it's written in the same
   transaction as `_upsert_user` — queryable, transactional, deterministic read,
   no AgentBase round-trip. AgentBase is insert-only / no-delete /
   similarity-recall — the wrong shape for a single authoritative mutable scalar.
   *Rationale check:* the kickoff endpoint is the **only** reader of
   `get_user_role`/`user_prefs` today, so the "keep role in the persona store"
   benefit is purely speculative — `users.role_id` wins until Feature 2 makes the
   persona store a real consumer (at which point role may *mirror* there).

2. **`jobTitle` → `roles.name` mapping = `roles.aliases` column** (`text[]`).
   Each role row lists the Entra `jobTitle` strings that map to it. Adding an
   alias = editing the same seed row used to add a role (consistent with the
   data-driven `data_plan` catalog). `resolve_role` normalizes the input
   (lowercase + collapse whitespace/punctuation) and scans roles, matching
   against each role's normalized aliases **and the role name itself** (implicit
   alias). **No algorithmic seniority stripping** — several pool names
   deliberately contain `Lead …` / `Associate …` (`Lead System Engineer` ≠
   `System Engineer`), so stripping would corrupt them. Variance handled by
   explicit aliases (word-order, e.g. Entra `"Applied AI …"` → pool `"AI Applied"`;
   seniority suffixes like `"… Intern"` listed as aliases, not stripped).
   **Unknown title → `None` → generic kickoff** (never guess a role: a wrong role
   pulls the wrong `data_plan`).

3. **`actorId` = OID does NOT belong to Feature 1.** Because role moves to
   `users.role_id` (Postgres, keyed by the OID-backed `users` row), Feature 1
   touches **no** `memory_client` actor logic. Specifically:
   - `project_facts/{mee-user}` (read by `chat_graph/context.py:53` via
     `search_project_record`, keyed by **meeting/project id**) is **shared across
     users by design** and MUST stay on the shared actor.
   - `user_prefs/{OID}` per-user keying matters **only for Feature 2** — and even
     there, only `user_prefs`, never `project_facts`.

4. **Domain = not stored.** `MS_TENANT_ID` already restricts login to the Entra
   tenant at the OAuth layer, so domain-based gating is redundant; and `domain`
   is derivable from the already-stored `email` (`email.split("@")[1]`) if ever
   needed. **No `domain` column, no gating logic.**

5. **Resolve at login, write once per login** (refresh on each login — jobTitle
   can change in the IdP), not per-kickoff.

## Components (small, testable — TDD; suite = `tests/meeting`, `asyncio_mode=auto`)

### A. Graph profile fetch — `meeting/auth/microsoft.py`
- `MicrosoftProvider.fetch_profile(access_token) -> dict` →
  `{job_title, department}` via
  `GET https://graph.microsoft.com/v1.0/me?$select=jobTitle,department`
  with `Authorization: Bearer <access_token>`.
- The access token from `acquire_token_by_authorization_code` is already a Graph
  token (`User.Read` scope) → **no new consent**.
- Called inside `exchange_code` after the token result; the resulting fields are
  added to `UserInfo`.
- **Best-effort:** any Graph error → `job_title=None` (login must never break).
- Extend `UserInfo` (`meeting/auth/base.py`) with `position: Optional[str]` (the
  `jobTitle`). `department` may be fetched for logging but is not persisted.

### B. Mapping — `meeting/services/role_mapping.py` (pure)
- `normalize(title) -> str` — lowercase, collapse whitespace/punctuation.
- `resolve_role(job_title, roles) -> str | None` — scan `roles`, match
  `normalize(job_title)` against each role's normalized `aliases` + the
  normalized role name; return the role name or `None`.
- Pure, no I/O — unit-tested per title.

### C. Repo + persistence
- Repo: `resolve_role_by_title(session, title) -> str | None` — loads roles
  (`list_roles`), delegates to the pure `resolve_role`.
- `User` model (`meeting/db/models.py`): add `role_id` (nullable FK→`roles.id`)
  and a `role` relationship.
- `_upsert_user` (`meeting/auth/routes.py`): after fetching the profile, resolve
  `role_id` from `info.position` and set `role_id`. **Refreshed on every login**
  (new user → set on create; returning user → re-resolve and update).
- **Migration chain fix (prerequisite):** the merge (`a2c61fb`) left **two
  migrations with revision id `"0016"`** — `0016_roles_pool` (this branch) and
  `0016_speaker_sample_paths` (master) — so alembic errors on any command. Fix by
  re-parenting `0016_roles_pool` to the end of the chain: rename → `0019`,
  `down_revision = "0018"`. Result: `0015 → 0016(speaker) → 0017(users_auth) →
  0018(word_ts) → 0019(roles_pool)`. The `roles` table is standalone, so moving
  it is safe.
- **New Alembic revision `0020`** (down_revision `0019`): add `users.role_id`
  (FK→`roles.id`), `roles.aliases` (`text[]` default `'{}'`), **+ idempotent
  reseed** of aliases into existing role rows by name (`UPDATE … WHERE name = …`).

### D. Kickoff re-wire — `meeting/api/chat.py`
- `kickoff_session` switches from `repo.get_or_create_dev_user` to
  `user: User = Depends(get_current_user)` (matches every other chat endpoint).
- `role = user.role` via `role_id` (load the relationship).
- Drop the `get_user_role(DEFAULT_ACTOR_ID)` role-read path and the persona
  branch of `_pick_role_name`.
- **Keep `KickoffRequest.role` as an optional dev override** — wins if provided,
  else the user's resolved role. `VITE_KICKOFF_ROLE` becomes that optional knob,
  no longer the primary source.
- `user_name = user.display_name`; persist the agent message as today.

### E. Frontend — `meeting_frontend_react`
- `ChatPane` / `api.chat.kickoff` stop **requiring** the env role; kickoff just
  works for the session user. Env role kept only as an optional override.

## Data flow

```
O365 login → exchange_code
  └─ MSAL token (Graph token) → fetch_profile(/me) → {jobTitle, department, mail, upn}
  └─ UserInfo{email, oid, tid, position, department, domain, token_cache}
callback → _upsert_user(info)
  └─ resolve_role_by_title(info.position) → role_name → roles.id → users.role_id
  └─ commit (same txn as user upsert)

open chat → POST /sessions/{id}/kickoff (empty thread)
  └─ get_current_user → user.role (via role_id)   [or KickoffRequest.role dev override]
  └─ role_data_plan → Redmine MCP reads → build_kickoff_messages → LLM → greeting
  └─ persist agent message → return {reply, role}
```

## Error handling (never block)

- Graph `/me` fails → `position=None` → `role_id=None` → generic kickoff. Login
  never breaks.
- Unknown jobTitle → `resolve_role` `None` → `role_id=None` → generic kickoff.
- All best-effort; login and chat-open never 500 on resolution/fetch failure.

## Testing (TDD; Graph + LLM + MCP mocked)

- **`test_role_mapping`** (new): `resolve_role` per title — alias hit, role-name
  hit, word-order variance, unknown→`None`, case/whitespace normalization.
- **`test_auth_microsoft`** (extend): `fetch_profile` parses a mocked Graph JSON
  response; `exchange_code` degrades to `position=None` when the Graph call errors.
- **`test_roles_repo`** (extend): `resolve_role_by_title` against seeded aliases.
- **`test_auth_routes`** (extend): `_upsert_user` sets `role_id` from
  `UserInfo.position`; returning-user refresh path updates it.
- **`test_kickoff_role_source`** (update): kickoff uses the authenticated
  `user.role_id`; the optional `KickoffRequest.role` override is still honored.
- **`test_seed_roles`** (extend): aliases present on seeded role rows.

## Constraints

- Backend runs on **:8002** (Vite **:8001** proxies `/api`,`/auth`→8002, `/ws`→9091).
- **Redmine MCP stays on the shared `REDMINE_API_KEY`** (per-user token deferred).
- **Migrations run normally by the user.** Same DB server; the env already holds
  the connection string (`DATABASE_URL_SYNC`, or derived from `DATABASE_URL` by
  `alembic/env.py`, psycopg2 sync). After the dup-`0016` re-parent + the new
  `0020`, the chain is single-headed and the user runs `alembic upgrade head`.
  Migration DDL is written idempotently (column adds + alias reseed) so a re-run
  is safe. (Supersedes the earlier out-of-band/stamp approach from
  `db-alembic-drift-remote-ahead` — the post-merge DB tracks the repo head.)

## Open input (fill when real Entra strings arrive)

The exact Entra `jobTitle` strings per user (annd2, hieunq3, nhihb, locdt4, …)
populate `roles.aliases`. Confirmed in brainstorm: strings vary by **word order**
(`"Applied AI"` ↔ `"AI Applied"`) and carry **extra/seniority words** the pool
name drops. The mapping mechanism (normalization + explicit aliases) is designed
to absorb this; the alias **values** are seed data, fillable without code change.

## Out of scope (Feature 2 / later)

- Learned style persona + `_agent_system_prompt` injection (separate brainstorm).
- `actorId` = OID ripple through `memory_client` (only needed for Feature 2's
  `user_prefs`; `project_facts` stays shared).
- Per-user Redmine token; pool-admin UI; per-org role customization.
