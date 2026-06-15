# OID → Position → Role Persona — Implementation Plan

**Extends:** `2026-06-13-role-persona-kickoff-design.md` (the kickoff feature, already built + committed `ee407a0`).
**Branch:** `feat/personalized-user-prompt`
**Status:** Planned, not started. Do brainstorm → spec → TDD in a fresh session.

## Goal

Replace the **`VITE_KICKOFF_ROLE`** dev stopgap. Derive each user's kickoff role
from their **O365 identity** at login: Microsoft Graph `/me` → `jobTitle`
(position) + domain → map to a `roles.name` → persist → the kickoff uses the
**logged-in user's real role** (`actorId` = their OID), per-user.

## Grounding (verified in code this session)

- **Login does NOT fetch position.** `UserInfo` (`meeting/auth/base.py:14`) =
  `{ms_oid, ms_tenant_id, email, display_name}` only. `jobTitle` is **not** a
  default ID-token claim.
- **Need a Graph call:** `GET /me?$select=jobTitle,department,mail,userPrincipalName`.
  The existing **`User.Read`** scope (`meeting/auth/microsoft.py:29`) already
  permits it — **no new consent**. The access token from code-exchange authorizes it.
- **Auth flow:** `routes.py` callback → `_upsert_user(info)` → `users` row
  (`ms_oid`, `email`, AES `refresh_token`). Session = signed httponly cookie.
- **Kickoff today (built):** `actorId` hardcoded `"mee-user"`
  (`memory_client.DEFAULT_ACTOR_ID`); `get_user_role` reads `user_prefs/{actorId}`;
  the role is currently supplied by the FE env (`KickoffRequest.role` +
  `_pick_role_name` in `meeting/api/chat.py`).
- **Roles pool:** Postgres `roles {id, name UNIQUE, description, data_plan,
  kickoff_prompt}`; `repo.get_role(name)` / `list_roles()`.

## Decisions (confirm in brainstorm)

1. **Where the resolved role lives — RECOMMENDED: `users.role_id` FK → `roles.id`.**
   Now that a real `users` table keyed by OID exists, storing the role on the
   user row is simpler, queryable, transactional, and drops the AgentBase
   round-trip. *Alternative:* AgentBase `user_prefs/{OID}` (the original spec's
   choice, made when there was no identity). Picking the column path means the
   kickoff reads `user.role` from the DB instead of `get_user_role`.
2. **`actorId` = OID** (drop hardcoded `"mee-user"`). Ripples through
   `memory_client.DEFAULT_ACTOR_ID` + every namespace (`project_facts/{oid}`,
   `user_prefs/{oid}`) → per-user memory.
3. **Resolve at login** (write once; refresh on each login), not per-kickoff.
4. **`jobTitle` → `roles.name` mapping (THE crux).** O365 `jobTitle` is free
   text; pool names are canonical. Needs a normalization/alias map (e.g.
   "Applied AI Intern" → "AI Applied"; strip seniority). *Needs the real Entra
   `jobTitle` strings to build it.*
5. **What "domain" is for** — clarify (gate to `@vng.com.vn`? multi-tenant?).

## Components (small, testable — TDD; suite = `tests/meeting`, `asyncio_mode=auto`)

- **A. Graph profile fetch** — add `MicrosoftProvider.fetch_profile(access_token)
  -> {jobTitle, department, upn, mail}`; extend `UserInfo` with `position`/
  `department`. Test by mocking the Graph HTTP response.
- **B. `resolve_role(job_title) -> role_name | None`** — pure mapping via an
  alias/normalization table (seed alongside `roles`). Unit-test each title →
  pool name + unknown → None + seniority stripping.
- **C. Persist** — `_upsert_user` writes the resolved role. If column path:
  Alembic `0017` adds `users.role_id` (nullable FK → `roles.id`).
- **D. `actorId` = OID** — thread the logged-in OID through `memory_client` +
  the kickoff; replace `DEFAULT_ACTOR_ID`.
- **E. Kickoff wiring** — endpoint resolves role from the logged-in user
  (`user.role_id` or `get_user_role(OID)`); deprecate `VITE_KICKOFF_ROLE` /
  `KickoffRequest.role` / `_pick_role_name` (optionally keep env as a dev
  override). Update `test_kickoff_role_source`.
- **F. FE** — stop sending the env role; kickoff just works for the session user.

## Open questions to resolve first

- The actual Entra `jobTitle` strings per user (annd2, hieunq3, nhihb, locdt4, …)
  → builds the decision-4 alias map.
- Decision 1 (AgentBase vs `users.role_id`) and decision 5 (domain use).

## Out of scope / constraints

- **Redmine MCP stays on the shared `REDMINE_API_KEY`** (per-user Redmine token
  deferred — separate phase).
- Backend runs on **:8002** (Vite :8001 proxies `/api`,`/auth`→8002, `/ws`→9091).
- **Don't auto-run `alembic upgrade`** — shared DB is stamped past repo head.

## Kickoff prompt (paste into a fresh session)

> Continue the role-persona kickoff feature on branch `feat/personalized-user-prompt`.
> Read first: memory `role-persona-kickoff-feature`; the design spec
> `docs/superpowers/specs/2026-06-13-role-persona-kickoff-design.md`; this plan
> `docs/superpowers/specs/2026-06-14-oid-role-persona-plan.md`; and the merged
> O365 auth code (`meeting/auth/microsoft.py`, `base.py`, `routes.py`, `tokens.py`).
>
> Goal: replace the `VITE_KICKOFF_ROLE` stopgap. At O365 login, fetch the user's
> position from Microsoft Graph `/me` (`jobTitle`) + domain, map `jobTitle` →
> `roles.name`, persist it, and make the kickoff use the logged-in user's real
> role (`actorId` = OID).
>
> Before coding, use the brainstorming skill to lock: (1) store the resolved
> role in a new `users.role_id` FK vs AgentBase `user_prefs`; (2) the
> `jobTitle`→`roles.name` mapping — I'll give you our real Entra `jobTitle`
> strings; (3) `actorId` = OID ripple through `memory_client`. Then writing-plans,
> then TDD build (mirror the existing kickoff tests in `tests/meeting`).
>
> Constraints: backend runs on `:8002` (Vite `:8001` proxies); Redmine MCP stays
> on the shared `REDMINE_API_KEY`; don't auto-run `alembic upgrade` (shared DB
> drift). I'll have our real O365 `jobTitle` values ready.
