# Mee — Feature Catalog

Each feature below is framed as **Use case → How it works → Key files**. This is the
"what & why" companion to the architecture notes in `CLAUDE.md` and the setup/flows in
`README.md`. Specs for individual features live under `docs/superpowers/specs/`.

---

## 1. Core data model — Project / Phiên / Segment

**Use case:** A team runs a weekly project that spans many meeting sessions. They want
one place per *project* that accumulates every *session's* transcript and minutes, plus
a rolled-up project view — not a flat pile of unrelated recordings.

**How it works:** Two-level hierarchy:
```
meetings   = a Project   (title, attendees, is_pinned, project_summary_json)
 └ recordings = a Phiên/session (session_label, started_at, mom_json, clean_segments)
     └ transcript_segments (per-sentence: seq, original_text, edited_text)
```
MoM is **per-recording** (`recordings.mom_json`); the project summary aggregates all of
a project's recording MoMs.

**Key files:** `meeting/db/models.py`, `meeting/db/repositories.py`, `meeting/api/meetings.py`.

---

## 2. Three input modes — paste / upload / live record

**Use case:** Sometimes the user pastes a transcript they already have; sometimes they
upload a recorded `.mp3/.wav/.m4a`; sometimes they record a live meeting from the mic.

**How it works:** Paste goes straight to segments. Upload hits `/api/transcribe`
(Whisper file upload) with auto-chunking of audio >24 MB into 10-min 16 kHz mono WAV
chunks. Live record streams mic audio over WebSocket (:9091) to Whisper streaming.

**Key files:** `meeting/app.py` (`/api/transcribe`, chunking), `frontend/src/hooks/useLiveRecording.ts`.

---

## 3. Speech-to-text — Whisper / PhoWhisper + diarization

**Use case:** Vietnamese-first transcription that keeps English tech terms intact, and
labels who said what when multiple people speak.

**How it works:** STT is OpenAI-compatible — VNG MaaS Whisper **or** self-hosted
PhoWhisper (8.85% WER) + pyannote diarization on an L40 GPU. The Whisper `initial_prompt`
is engineered to anchor Vietnamese while preserving English terms; known-hallucination
phrases are filtered, and audio is auto-chunked when large.

**Key files:** `meeting/app.py` (`_build_whisper_prompt`, `WHISPER_HALLUCINATIONS`),
`tools/phowhisper-server/`, `meeting/services/local_diarize.py`, `parallel_diarize.py`.

---

## 4. Clean transcript — TipTap WYSIWYG editor

**Use case:** Raw STT output has errors. The user wants to fix names/terms and mark
commitments/decisions inline before generating minutes, so the MoM is higher quality.

**How it works:** A TipTap rich-text editor (bold/italic/lists/headings + tag chips)
writes to `recordings.clean_segments` (JSONB `edited_html`/`edited_text`) with 1.5s
auto-save. MoM generation **prefers `edited_text`** over raw transcript when present.

**Key files:** `frontend/` (TipTap editor), `meeting/services/transcript_cleaner.py`,
`meeting/services/clean_orchestrator.py`.

---

## 5. MoM per recording — Biên bản phiên họp

**Use case:** After a session, produce structured minutes: agenda, action items
(who/deadline), decisions, commitments, blockers — for *this* session specifically.

**How it works:** `mom_graph.py` is a LangGraph `StateGraph`
(`load_transcript → read_memory → generate_mom → save_results`), compiled with
`AsyncPostgresSaver` and `thread_id = recording_id` so a re-run resumes from the failed
node. The LLM call uses **map-reduce** for long transcripts (tuned for 8K-context
Qwen3-8B) and strips Qwen3 `<think>` tags.

**Key files:** `meeting/graphs/mom_graph.py`, `meeting/note_generator.py`, `meeting/report_generator.py`.

---

## 6. Project summary — cross-session rollup

**Use case:** "Where does the whole project stand?" — a timeline of decisions across all
sessions plus an LLM narrative, instead of reading every session's MoM.

**How it works:** `project_summarizer.py` aggregates every recording's MoM by
`started_at` into a timeline + narrative, stored in `meetings.project_summary_json`.

**Key files:** `meeting/services/project_summarizer.py`, `meeting/api/meetings.py`.

---

## 7. Hybrid cross-meeting memory (retrieval)

**Use case:** "What did we decide about caching last month?" — recall relevant facts
from *other* meetings, matching both keywords and meaning.

**How it works:** `memory_service.py` does hybrid retrieval over `memory_events`
(pgvector): keyword tsvector + semantic bge-m3 (1024-dim) + RRF fusion + optional LLM
rerank, scoped by `user_id`/`meeting_id`. `memory_events` is written by the MoM graph.

**Key files:** `meeting/services/memory_service.py`, `embedding.py`, `reranker.py`,
migration `0006` (pgvector + IVFFlat).

---

## 8. Agent-memory sync (Postgres → AgentBase projection)

**Use case:** The chat agent needs a compact, always-available "current project state"
to ground answers without hitting Postgres on every turn.

**How it works:** A one-way projection distills each project's summary + MoMs into one
newest-wins AgentBase record in `project_facts/mee-user`, keyed by a
`[mee-sync project=… hash=…]` marker for content-hash change detection. Runs as a batch
sweep **and** event-driven after a MoM/summary save. `load_context` recalls it as
`project_memory` and flags staleness (Q1 check) with a non-blocking re-sync.

**Key files:** `meeting/services/memory_sync.py`, `memory_sync_runner.py`,
`meeting/memory_client.py`, `scripts/sync_memory.py`.
Spec: `docs/superpowers/specs/2026-06-11-agent-memory-sync-design.md`.

---

## 9. Chat-captured knowledge — remember_fact / forget_fact

**Use case:** "Gọi tôi là Ronaldo", "deadline dời sang 30/06" — durable facts the user
states (or the agent deduces) should be remembered across sessions, and turn-off-able
without deleting.

**How it works:** Auto-run tools write `[mee-fact …]` records to AgentBase
(`user_prefs/<ms_oid>` or `project_facts/<meeting_id>`), background fire-and-forget.
Newest-wins-per-`key` with an `active` flag: `forget_fact` writes a newer `active=0`
tombstone (hide, no delete); `remember_fact` reactivates. `load_context` recalls them
into a capped "Ghi nhớ" prompt block.

**Key files:** `meeting/services/tools/remember_fact.py`, `meeting/memory_client.py`,
`meeting/graphs/chat_graph/context.py`, `scripts/dump_agent_memory.py`.
Spec: `docs/superpowers/specs/2026-06-16-chat-knowledge-capture-design.md`.

---

## 10. Chat agent (unified tool-calling)

**Use case:** A conversational assistant that answers questions about meetings and
performs actions (create tasks, email, query Redmine) in natural Vietnamese.

**How it works:** A LangGraph agent speaking **native OpenAI tool-calling** (Path A).
`classify_intent` routes (only `/pm-agent` opts into the A2A branch); otherwise the
unified agent loops `agent → agent_tools → (agent_approve → agent_execute) → …`. Replay-
safe: only `agent_approve` interrupts. Leaked `<think>` reasoning is stripped from
replies; non-JSON classifier output is tolerated.

**Key files:** `meeting/graphs/chat_graph/` (`agent.py`, `classify.py`, `builder.py`,
`runner.py`, `context.py`), `meeting/graphs/_chat_prompts.py`, `_chat_serde.py`.

---

## 11. Chat HITL — approve/reject side-effect tools

**Use case:** The agent should never email someone or create tasks without the user
seeing and approving exactly what it will do.

**How it works:** Tools register with `side_effect=True`. `agent_tools` defers the first
side-effect call; `agent_approve` surfaces an editable pending-action card; the API
persists it; approve/reject resumes (`agent_execute` runs it, or finishes on reject).
One action approved per round. (`remember_fact`/`forget_fact` are deliberately
`side_effect=False` — auto-run.)

**Key files:** `meeting/api/chat.py`, `meeting/graphs/chat_graph/agent.py`,
`meeting/services/tools/_registry.py`.

---

## 12. Agent tool suite

**Use case:** Give the agent a focused, auditable set of capabilities.

**How it works:** Each tool self-registers via the local `@tool(name, description,
side_effect, schema)` decorator; `meeting_id` is injected server-side. Tools:
`create_task`, `send_email`, `switch_meeting`, `list_meetings`, `list_recordings`,
`recording_mom`, `retrieve`, `search_transcript`, `remember_fact`, `forget_fact`, plus
dynamically-discovered Redmine MCP tools. Some are detached from the LLM surface
(`retrieve`, `search_transcript`) since memory replaces them for Q&A grounding.

**Key files:** `meeting/services/tools/` (one module per tool).

---

## 13. Redmine integration (MCP)

**Use case:** "Sync this meeting's action items to Redmine", "what issues are overdue?"

**How it works:** Redmine tools are discovered over MCP at startup. Reads use a
deterministic in-code table formatter (no LLM re-render — the #28815 fix). Writes
(`create_redmine_issue`/`update_redmine_issue`) are HITL-gated. `create_task` builds an
editable task list from MoM action items, approved once for the whole batch, then applied
directly via MCP in `agent_execute`.

**Key files:** `meeting/services/tools/redmine.py`, `redmine_mcp_client.py`,
`meeting/graphs/chat_graph/redmine_format.py`, `pm.py`.
Specs: `2026-06-12-redmine-mcp-migration-design.md`, `…-redmine-read-deterministic-formatter-design.md`.

---

## 14. Per-user Redmine key (AgentBase Identity)

**Use case:** Each user should act in Redmine as *themselves*, not a shared service
account — so assignments/authorship are correct.

**How it works:** The Redmine proxy resolves a per-user key (dev-fallback → ms_oid →
cached AgentBase Identity key); a missing key surfaces `redmine_key_missing` with a
consent gate + red banner in the FE. `GET /api/redmine/status` reports linkage.

**Key files:** `meeting/services/identity_client.py`, `meeting/services/tools/redmine.py`,
`scripts/bootstrap_redmine_identity.py`.
Spec: `docs/superpowers/specs/2026-06-15-per-user-redmine-key-identity-design.md`.

---

## 15. Microsoft 365 login + identity

**Use case:** Sign in with the company Microsoft account; the agent knows who "tôi" is.

**How it works:** Azure OAuth (callback registered on :8001). The signed-in user's
display name, email, role, and Entra `ms_oid` are loaded in `load_context` and injected
into the agent prompt so it can scope role-based behavior and per-user memory.

**Key files:** `meeting/api/` (auth routes), `meeting/db/models.py` (`User.ms_oid`),
`meeting/graphs/chat_graph/context.py`.
Spec: `docs/superpowers/specs/2026-06-14-o365-login-design.md`.

---

## 16. Role persona + proactive kickoff

**Use case:** When a user opens chat, greet them in a way tailored to their role and
proactively orient them — without manual configuration.

**How it works:** OID → `jobTitle` → `users.role_id` → persona; the kickoff reads the
user's role from `user_prefs/<oid>` (best-effort) and seeds a role-aware opening.

**Key files:** `meeting/services/kickoff.py`, `meeting/memory_client.py` (`get_user_role`),
`meeting/services/role_mapping.py`.
Specs: `2026-06-13-role-persona-kickoff-design.md`, `2026-06-14-oid-role-persona-design.md`.

---

## 17. Role auto-classification

**Use case:** Users without a mapped role should still get one inferred from their job
title, kept fresh without manual edits.

**How it works:** `users.position` + an LLM `classify_role` fallback + alias mapping; a
manual/cron worker (`scripts/classify_roles.py`) backfills roles. Uses minimax with a
reasoning-field fallback (the max_tokens trap fix).

**Key files:** `meeting/services/role_mapping.py`, `scripts/classify_roles.py`,
migration `0021`.
Spec: `docs/superpowers/specs/2026-06-15-role-autoclassify-design.md`.

---

## 18. User-scoped chat sessions

**Use case:** Each user has their own chat history (sidebar list, new/rename/delete),
decoupled from any single meeting, but each turn can still be grounded on a chosen
project.

**How it works:** Sessions are per-user (not per-meeting); the UI passes a per-turn
`meeting_id` for grounding. Sidebar supports new session, rename (PATCH), and hard-delete.

**Key files:** `meeting/api/chat.py`, `meeting/graphs/chat_graph/context.py`,
migration `0022`.
Spec: `docs/superpowers/specs/2026-06-15-user-scoped-chat-sessions-design.md`.

---

## 19. Cross-meeting speaker identification (voiceprints)

**Use case:** Recognize the same speaker across different meetings without re-enrolling.

**How it works:** A per-user voiceprint dictionary (`speaker_voiceprints`) enables
zero-shot speaker ID during diarization.

**Key files:** `meeting/db/models.py` (`SpeakerVoiceprint`), `meeting/services/speaker_matcher.py`.

---

## 20. UX — i18n, theme, sidebar management, streaming

**Use case:** A polished bilingual UI with familiar conventions.

**How it works:** Full VI/EN i18n, dark/light theme (localStorage, dark default),
sidebar context menu (Share/Rename/Pin/Delete project; delete phiên), and streaming chat
responses.

**Key files:** `frontend/src/i18n.ts`, `src/store/AppContext.tsx`,
`src/api/client.ts`.
Spec: `docs/superpowers/specs/2026-06-10-chat-ux-streaming-design.md`.

---

## Cross-cutting: one DATABASE_URL, three drivers

Not a user feature, but essential context: the same Postgres URL is normalized three
ways — asyncpg (app), psycopg2 (Alembic), psycopg3 (LangGraph checkpointer). pgvector is
required. See `CLAUDE.md` → "Critical gotchas".
