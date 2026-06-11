# Agent Memory Sync — Postgres → AgentBase (v1)

**Date:** 2026-06-11 · **Branch:** `feat/personalized-user-prompt`
**Status:** Design approved (4 decisions locked with user). Ready for plan → TDD.

## Goal

Give the chat agent an **agent-optimized memory layer** separate from Postgres. Postgres
stays the system of record (raw `mom_json`, `project_summary_json`, transcripts). AgentBase
Memory holds a **distilled, per-project current-state projection** the agent recalls cheaply
at chat time instead of re-reading large JSON blobs.

AgentBase is a **rebuildable cache**, never authoritative.

## Decisions (locked)

1. **One-way** Postgres → AgentBase. No write-back. Live chat-turn writing is out of v1 scope.
2. **Upsert one evolving current-state record per project** (overwritten each sync), not
   append-only history.
3. **Standalone script** `scripts/sync_memory.py` (cron/manual), not in-process background.
4. **Change detection** — only re-distill projects whose source data changed.

Deferred (noted for future): **option (b)** event-driven sync hooked into `save_results` /
`project_summarizer`. Revisit after (a) ships.

## Architecture

```
scripts/sync_memory.py
  └─ for each Meeting (project) not soft-deleted:
       1. gather source = project_summary_json + [r.mom_json for r in recordings]
       2. source_hash = sha256(canonical_json(source))
       3. fetch existing AgentBase record for this project (namespace=project_facts)
            └─ if record.metadata.source_hash == source_hash: SKIP (unchanged)
       4. distill(source) -> condensed current-state text  [LLM call]
       5. upsert record (payload=state text, metadata={project_id, source_hash, synced_at})
```

### Change detection — content hash (no schema change)

Neither `meetings` nor `recordings` has `updated_at`; adding a migration is risky now
(DB drift: remote `0016`, repo `0015`). So the watermark is a **sha256 of the canonical-JSON
of the distillation inputs**, stored in the AgentBase record's metadata. Next run compares;
equal → skip the LLM call. Fully stateless, no local sync-state table.

### Distillation input (from schema, `meeting/db/models.py`)

- `Meeting.project_summary_json` — already an aggregate timeline/narrative.
- Each `Recording.mom_json` — per-session action_items / decisions / commitments / blockers.
- `Meeting.title`, `Recording.started_at` for ordering/labels.

Output: one condensed "current state" block per project — phase, open decisions, active
blockers, who-owns-what, recent progress. Prompt lives in a new
`meeting/services/memory_sync.py` (reuses the per-service `OpenAI(...)` pattern; no shared
client singleton — see CLAUDE.md).

### AgentBase write target

- Memory `memory-34e0820d-…` (`smem-mee-prod`), strategy namespace `project_facts` (SEMANTIC).
- Auth + endpoints per `scripts/probe_memory_read.py` and memory `agentbase-memory-api-setup`.
- **Upsert semantics:** AgentBase records are content-addressed per namespace; if it has no
  native "upsert by key," delete-then-insert the project's record (key = `project:{id}` in
  metadata). **Open item for implementation:** confirm the records write/delete endpoint
  shape via a probe before coding the writer (the probe script only exercises read + search).

### Identity

`actor_id` is hardcoded `"mee-user"` in `memory_client.py`; app runs as `dev_user`. For v1
project-state records are **project-scoped, not actor-scoped** (a project's state is shared),
so they live in the `project_facts` namespace keyed by `project_id` — actor identity is not
required for this projection. Per-user `user_prefs` memory is a separate, later piece.

## New / changed files

- `scripts/sync_memory.py` — entrypoint: iterate projects, hash, distill, upsert. Mirrors
  `scripts/backfill_embeddings.py` structure (async engine, `--dry-run`, per-project logging).
- `meeting/services/memory_sync.py` — `distill_project_state(summary, moms) -> str` (LLM) +
  `canonical_source_hash(...)`. Pure-ish, unit-testable without DB or network.
- `meeting/memory_client.py` — extend: add `search_project_record(project_id)` /
  `upsert_project_record(project_id, text, source_hash)` (currently write-only event poster).

## Testing (no DB in dev env → fakes)

- Unit: `canonical_source_hash` stable across key-order / equal inputs; changes when a MoM
  changes. (pure, no network)
- Unit: `distill_project_state` prompt-assembly with a fake LLM client (DI seam).
- Skip-path: given a fake AgentBase client returning a matching `source_hash`, sync makes
  **zero** distill/upsert calls for that project.
- Follow existing `tests/meeting` fake conventions (no live DB/network).

## Open items to resolve during implementation

1. **Probe the AgentBase records WRITE/DELETE endpoint** (read side is known; write side for
   `memory-records` is not yet exercised) — do this first, it gates the writer.
2. Confirm `project_summary_json` key shape against `meeting/services/project_summarizer.py`
   so the distiller reads real fields.
3. Decide dry-run default (recommend `--dry-run` prints distilled text + hash, writes nothing).
```
