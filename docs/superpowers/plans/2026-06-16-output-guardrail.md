# Plan: output guardrail for the chat agent

Status: PROPOSED (not started) — 2026-06-16

## Problem
The agent has **output hygiene** (strip_think, leaked-tool-call recovery, deterministic
Redmine formatter, round-cap graceful synthesis, "no premature narration" prompt rule,
HITL on side-effect actions) but **no output guardrail** that checks the *reply itself*.
Observed failure: asked "Meeting 1 đi", the model thrashed and then drifted to an
unrelated GOAT/Ronaldo answer (the remembered "Ronaldo" nickname pulled it off-topic).
The round-cap + prompt fixes (commit `f5e0b82`) mitigate the symptom but nothing verifies
that a reply actually answers the user's question from the data on hand.

## Goal
Catch and correct **drift / ungrounded** replies before they reach the user — cheaply,
without a full safety stack — and lay a seam for future moderation/PII if needed.

## Gaps today (none implemented)
- Grounding/relevance check (reply vs. user question + tool results).
- Content moderation (toxicity/profanity).
- PII detection/redaction in replies.
- Prompt-injection / jailbreak defense.
- Output schema/format validation of `final_reply`.

## Proposed v1 — grounding/relevance guard (highest value)
A lightweight post-turn check on `final_reply` before `save_reply`:
- **Cheap heuristic first:** if the turn called read tools (`recording_mom`/`list_*`/
  `retrieve`) but the reply references none of their content, or the reply is off-topic
  vs. the user message → flag.
- **LLM judge fallback (only when flagged):** one tiny call — "Does this reply answer
  the user's question using the tool results? yes/no + reason." On `no`, either
  regenerate once (tool-less, grounded) or return a safe "mình chưa chắc, bạn hỏi rõ hơn
  nhé" instead of the drifted text.
- Best-effort + bounded (max one re-gen) so it never loops or blocks.
- Wire as a node between the agent loop and `save_reply`, or fold into the final
  synthesis path so it shares the tool-less call.

### Alternatives considered
- **Prompt-only** (done in `f5e0b82`): necessary but not sufficient — models still drift.
- **Always-on LLM judge every turn:** too costly; gate it behind the cheap heuristic.
- **External moderation API:** overkill for an internal Vietnamese meeting tool; revisit
  only if untrusted users/content appear.

## Refactor seam (do alongside)
`_strip_think` is duplicated in **6 places** (`memory_sync`, `meeting_resolver`,
`role_mapping`, `kickoff`, `note_generator` regex, + `_chat_serde.strip_think`). When
adding the guardrail, extract a shared **output-sanitizer module**
(`meeting/services/output_guard.py`?) that hosts `strip_think` + the grounding check, and
collapse the duplicates.

## Docs follow-up (cheap, unrelated win)
`docs/AGENT.md` §4b lists Redmine MCP tools as "representative". The real discovered set is
now in `.mcp_redmine_tools_cache.json` (e.g. `get_redmine_projects`, `get_field_metadata`,
`create_redmine_issue`, …). Update §4b with the actual names + note they're cached at
`.mcp_redmine_tools_cache.json`.

## Open questions
- Regenerate-once vs. ask-clarify on a drift flag? (UX vs. latency.)
- Should the guard run for action turns too, or only Q&A/grounded turns?
- Threshold for the cheap heuristic before paying for the judge call.

## Related
- commit `f5e0b82` (round-cap graceful finish + anti-thrash prompt) — symptom mitigation.
- `docs/superpowers/specs/2026-06-16-chat-knowledge-capture-design.md`.
- `docs/AGENT.md` (HITL = action guardrail; this plan adds the *content/output* guard).
