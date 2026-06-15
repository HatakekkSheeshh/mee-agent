# Role Auto-Classify (Background) — Design Spec

**Branch:** `feat/personalized-user-prompt`
**Status:** Design approved 2026-06-15. Spec for a fresh-session TDD build.
**Builds on:** Feature 1 (`2026-06-14-oid-role-persona-design.md`, BUILT + applied — `users.role_id`, `roles.aliases`, `resolve_role`). Memory: `role-autoclassify-feature`.

## Goal

Remove Feature 1's dead-end where an O365 `jobTitle` that doesn't match the pool
leaves the user with a generic kickoff forever. A background job classifies each
unmatched `jobTitle` into the **best-fit existing role** via an LLM and writes the
`jobTitle` back as an **alias** — so the next `resolve_role` is a free,
deterministic match. The pool *converges* (self-populating aliases); it does NOT
grow new role rows.

## Decisions (locked in brainstorm 2026-06-15)

1. **Classify INTO the existing pool, not grow it.** On a `resolve_role` miss, an
   LLM picks the best-fit existing role; the raw `jobTitle` is appended to that
   role's `aliases`. No auto-created role rows (the `data_plan` of an invented
   role is exactly what an LLM gets wrong; auto-create also fragments the curated
   pool).
2. **Plain service function, NOT a LangGraph node.** It's a single stateless LLM
   call firing outside any graph turn — mirrors the per-service-client pattern
   (`note_generator`, `transcript_cleaner`, …). A node would only be warranted
   for an interactive HITL confirm, which we rejected (silent best-effort).
3. **Trigger = standalone cron script** (`scripts/classify_roles.py`, like
   `scripts/sync_memory.py`). Off all request paths; observable; retriable.
4. **Auto-write with a confidence guard.** The alias is written immediately (no
   review queue). Guards: the returned role name must be in the pool (reject
   hallucinations); below a confidence threshold (~0.6) the user is left
   unmatched (generic kickoff) rather than mislabeled.
5. **Classify to a role** (returns a pool role name), not to a bare `data_plan` —
   the role carries its `data_plan`, and the alias write-back targets a specific
   role.

## Prerequisite

Feature 1 stored only `role_id`, not the raw `jobTitle`. The worker needs the raw
title to find + classify unmatched users → **add `users.position`** and persist
it at login.

## Components (small, testable — TDD; suite `tests/meeting`, `asyncio_mode=auto`)

### A. `users.position` column + persist at login
- Alembic `0021` (idempotent, guarded like `0019`/`0020`): add `users.position`
  (nullable text).
- `User` model: add `position: Mapped[Optional[str]]`.
- `_upsert_user` (`meeting/auth/routes.py`): set `user.position = info.position`
  on both create + returning paths (`UserInfo.position` already exists from
  Feature 1).

### B. `classify_role` service fn — `meeting/services/role_mapping.py`
- `classify_role(job_title, roles, *, generate, threshold=0.6) -> str | None`.
- Builds an LLM prompt grounding each role's `name` + `description` + `data_plan`,
  instructs the model to pick the single best-fit role **from the closed set** or
  answer `NONE`, returning a compact structured form (role name + confidence
  0–1). `generate(messages) -> str` is injected (unit-testable, no network).
- Parse + `_strip_think`; **reject any role name not in the pool**; below
  `threshold` → `None`. Returns the matched pool role name or `None`.

### C. Alias write-back + role_id backfill — repo helpers
- `add_role_alias(session, role_id, alias)` — `UPDATE roles SET aliases =
  array_append(aliases, :alias) WHERE id = :role_id AND NOT (:alias = ANY(aliases))`
  (dedup; single-instance cron → no concurrency concern).
- Backfill: set `users.role_id` for the classified user so the next kickoff is
  tailored without waiting for re-login.

### D. Worker — `scripts/classify_roles.py`
Mirrors `scripts/sync_memory.py` (async session via `AsyncSessionLocal`, LLM via
`_llm_client`/`_llm_model`, arg parse, logging):
```
load unmatched users: position IS NOT NULL AND role_id IS NULL
load roles pool once
for each user:
  name = resolve_role(user.position, roles)            # deterministic first (cheap)
  if not name:
      name = classify_role(user.position, roles, generate=…)   # LLM fallback
      if name: add_role_alias(role.id, user.position)  # self-heal for next time
  if name: user.role_id = role.id                      # backfill
  commit per user; one failure logged, never sinks the batch
```

## Error handling (never break)
- LLM failure / unparseable / out-of-pool name / low confidence → skip that user
  (stays unmatched → generic kickoff). Per-user try/except; batch continues.
- Worker is idempotent + re-runnable; dedup guard prevents duplicate aliases.

## Testing (TDD; LLM mocked via injected `generate`)
- `classify_role`: clear title → pool role; LLM returns out-of-pool name → `None`;
  below threshold → `None`; `<think>` stripped; `NONE` answer → `None`.
- `add_role_alias`: appends when absent; no-op when already present (dedup) —
  against a fake/seeded row.
- worker user-selection: picks only `position IS NOT NULL AND role_id IS NULL`.
- `_upsert_user` sets `user.position` (extend `test_auth_role_persist.py`).

## Migration / run
- Alembic `0021` authored idempotently (guarded column add). User applies via
  `alembic upgrade head` (same as Feature 1; env loads `.env`).
- Worker run manually or via cron: `venv/bin/python scripts/classify_roles.py`.

## Out of scope
- Auto-creating new role rows (rejected). Review-queue/HITL (rejected — auto-write).
- Feature 2 (learned style persona) — separate.
- Confidence-threshold tuning UI; per-org pools.
