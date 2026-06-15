# Per-user Redmine key via AgentBase Identity — design

**Date:** 2026-06-15
**Branch:** `feat/personalized-user-prompt`
**Status:** Design approved (direction); implementation deferred to a later session.

## Problem

Today the Redmine MCP server is authenticated with a single **process-global**
`REDMINE_API_KEY` env var (`meeting/services/redmine_mcp_client.py` — docstring
literally: *"No per-user auth"*). That is wrong for the real model: **each VNG
user has their own Redmine API key, and a key only grants access to the projects
that user is a member of.** One shared key means every user sees one user's
project scope.

We also have no signal to the user when Redmine (the 15 MCP tools) or pm-agent
is unreachable — failures surface only as opaque tool errors mid-chat.

## Goal

1. Stop hardcoding / globally sharing the Redmine key.
2. On login, check whether the current user can reach **the Redmine MCP tools**
   and **pm-agent**.
3. If the user's Redmine key is missing/invalid, open a **gate** for them to
   supply their own key (stored in GreenNode AgentBase, not Mee's DB/`.env`).
4. While anything is inaccessible, show a **warning banner with red text**.

## Key discovery: AgentBase Identity → Outbound Auth → `delegated` provider

GreenNode AgentBase Identity service
(`https://agentbase.api.vngcloud.vn/identity/api/v1`) provides outbound-auth
providers. The fit for "end-users provide their own API keys" is the
**`delegated` API-key provider**:

- Created **once**: `POST /outbound-auth/delegated-api-key-providers`
  `{"name": "redmine"}` — **no key stored upfront**; keys come from end-users.
- Each user's key is keyed by an **`agentUserId`**.
- Retrieved at runtime: `POST /outbound-auth/delegated-api-key-providers/
  {providerName}/agent-identities/{agentName}/api-key` with `agentUserId` +
  a `returnUrl` (which must be in the agent identity's `allowedReturnUrls`).
- `request-key` triggers a **user-federation consent flow** (returns a hosted
  URL; the user provides their key there; callback to `returnUrl`). The raw key
  never passes through Mee's chat/LLM/logs — which the AgentBase skill docs
  explicitly require.

**Mapping to Mee:** `agentUserId = user.oid` (the Azure OID we already store).
Prerequisite: one **agent identity** (`POST /agent-identities`) whose
`allowedReturnUrls` includes Mee's callback URL.

Auth to AgentBase: IAM client-credentials using `GREENNODE_CLIENT_ID` /
`GREENNODE_CLIENT_SECRET` (already in `.env`, used by the Memory integration).

### Decided UX: redirect-based consent gate (not an in-app text field)

The gate redirects the user to the AgentBase-hosted consent page (from
`request-key`) where they enter their Redmine key, then AgentBase calls back to
Mee. Chosen over an in-app text field for security (no raw secret through
Mee/LLM) and because it is the provider's intended flow.

## Architecture

### Backend

**1. `meeting/services/identity_client.py` (new).**
Thin async client for the AgentBase Identity service. Responsibilities:
- IAM token (client-credentials), reusing the existing token helper pattern.
- `ensure_provider()` / `ensure_identity()` — idempotent bootstrap (best-effort,
  logged; the provider + identity are created once and normally already exist).
- `request_user_key(agent_user_id, return_url)` → starts/returns the delegation
  flow (hosted URL) for a user who has no key yet.
- `get_user_key(agent_user_id)` → the user's stored Redmine key, or `None`.
- Per-service base-URL validation against the documented domain.

> **Implementation probe required:** the exact request/response shape of
> `request-key` (sync key return vs. async consent URL + callback) is in the
> skill's `references/usage.md`, not yet read. First implementation step is a
> read-only probe of that reference + a live `list`/`get` call to pin the
> contract before writing the client. No guessing field names.

**2. Per-user key resolution at tool-execution time.**
`meeting/services/tools/redmine.py::_proxy` currently calls the process-global
`get_redmine_mcp_client()` (one env key). Change so the executor resolves the
**current user's** key — `execute_tool(...)` already receives `user_id`:
- Look up `user.oid` from `user_id`.
- `get_user_key(oid)` (in-memory TTL cache, e.g. 5 min, to avoid an AgentBase
  round-trip per tool call).
- Build/forward a **per-call Bearer** to the MCP client (the client gains a
  per-call key param, or a per-user client is constructed). The global
  `REDMINE_API_KEY` env var is removed as the source of truth; it MAY remain as
  a dev/local fallback only, gated behind an explicit flag.
- If the user has no key → return a structured `{"error": "redmine_key_missing"}`
  so the agent surfaces it cleanly (pairs with the loop-side guard already
  shipped — the turn finishes, no retry).

**3. Accessibility probe — `GET /api/redmine/status` (new).**
Called by the FE after login. For the current user returns per-service status:
- `redmine_key_present`: AgentBase has a key for this OID.
- `redmine_tools_ok`: the key resolves and a cheap probe succeeds (tool count
  == expected, or a `users/current.json`-style ping). Expected tool count is a
  named constant (the "15 tools"), not a magic number; mismatch is reported.
- `pm_agent_ok`: pm-agent reachable (lightweight ping; pm-agent keeps its own
  `TOKEN_AUTHEN_PM_AGENT` + MS token — NOT fixable via the Redmine key).
- On `redmine_key_present == false`, includes the consent `gate_url` from
  `request_user_key`.

**4. Gate completion.**
The AgentBase consent callback returns to a Mee `returnUrl`; the FE re-probes
`/api/redmine/status` afterward to confirm the key now resolves. (If the live
contract turns out to be a direct key submission rather than a redirect, add
`POST /api/redmine/key` as a fallback — decided during the implementation probe.)

### Frontend (`meeting_frontend_react`)

**5. Post-login status check + gate + banner.**
- After login (where `/auth/me` resolves), call `/api/redmine/status`.
- If `redmine_key_present` is false → open a **gate** (modal/redirect) pointing
  the user to the AgentBase consent `gate_url`; on return, re-probe.
- While any service is `*_ok == false`, render a **warning banner with red
  text** (extend the existing `WelcomeBanner.tsx` pattern). The banner names
  which service is down (Redmine vs pm-agent) and, for a missing key, offers a
  "Nhập Redmine key" action that opens the gate.
- i18n VI/EN strings (project already has `src/i18n.ts`).

## Data flow

```
login → FE GET /api/redmine/status (user OID)
  ├─ key present + tools ok + pm ok → no banner
  ├─ key missing → banner(red) + gate → AgentBase consent (user enters key)
  │                   → callback → FE re-probe → ok
  └─ key present but tools/pm down → banner(red), names the failing service

chat turn calling a Redmine tool:
  execute_tool(user_id) → resolve OID → get_user_key(OID) [TTL cache]
    → per-call Bearer → MCP call_tool
    → missing key → {"error":"redmine_key_missing"} → agent finishes turn
```

## Error handling

- AgentBase unreachable / IAM token failure → status endpoint reports
  `redmine` unknown (not a hard 500); banner shows a generic red warning; tools
  fail closed with a structured error (never silently fall back to a shared key).
- Key resolves but Redmine rejects it (revoked/wrong) → `redmine_tools_ok=false`;
  banner prompts re-entry via the gate.
- pm-agent down → `pm_agent_ok=false`; banner red; no gate (not key-fixable).
- All new external calls fail closed and are logged; no secret in logs.

## Testing (TDD, offline)

- `identity_client.py`: pure parsing/URL-building + a faked HTTP transport
  (mirror `test_pm_agent_client.py` / `redmine_mcp_client` parse tests). No live
  AgentBase in the suite.
- Per-user key resolution in `_proxy`: inject a fake key-resolver; assert the
  Bearer forwarded per call matches the user's key, and missing-key →
  `{"error":"redmine_key_missing"}`.
- `/api/redmine/status`: fake the identity client + MCP probe; assert per-service
  flags and `gate_url` presence when key missing.
- FE: banner renders red + names the failing service; gate opens when key
  missing; hidden when all ok (component test).

## Scope / non-goals

- pm-agent remains on its own credentials; this work only *reports* its status.
- No migration of historical data; the global `REDMINE_API_KEY` is retired as
  the runtime source of truth (optional dev-only fallback behind a flag).
- OAuth2 / 3LO providers are out of scope (delegated apikey only).

## Open items to resolve at implementation start (probe first, no guessing)

1. Exact `request-key` contract (sync vs. consent-redirect; field names) from
   `references/usage.md` + a live `list`/`get` probe.
2. Whether an agent identity + `redmine` delegated provider already exist for
   this account (`list` first), and the agent identity `name` to use.
3. Mee's `returnUrl` to whitelist in the agent identity's `allowedReturnUrls`.
4. Confirm `user.oid` is populated for all current users (it backs `agentUserId`).
