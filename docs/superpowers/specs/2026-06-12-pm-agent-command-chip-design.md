# `/pm-agent` command chip — FE design

**Date:** 2026-06-12 · **Branch:** `feat/personalized-user-prompt` · **Scope:** frontend only (`meeting_frontend_react`)

## Goal

Visually mirror the backend `/pm-agent` opt-in in the React chat input. When a user types
`/pm-agent …` at the start of the chat box, show a floating "command pill" above the input,
and render a `pm-agent` chip on the sent user message (with the command stripped from the
displayed text). No backend changes.

## Backend contract (mirror exactly)

`meeting/graphs/chat_graph/classify.py::_pm_agent_opt_in`:
- `stripped = msg.lstrip()`
- opted-in iff `stripped[:9].lower() == "/pm-agent"` (case-insensitive, leading whitespace OK,
  no separator required — `/pm-agentXYZ` also matches)
- cleaned message = `stripped[9:].lstrip()`

The backend strips the command itself, so the FE **sends the full text** (including `/pm-agent`)
and only uses the cleaned form for display.

## Components

### 1. Pure helper — `src/utils/pmAgent.ts`
```ts
export const PM_AGENT_COMMAND = "/pm-agent";
export function pmAgentOptIn(msg: string): { opted: boolean; cleaned: string } {
  const s = msg.replace(/^\s+/, "");
  if (s.slice(0, PM_AGENT_COMMAND.length).toLowerCase() === PM_AGENT_COMMAND)
    return { opted: true, cleaned: s.slice(PM_AGENT_COMMAND.length).replace(/^\s+/, "") };
  return { opted: false, cleaned: msg };
}
```
Single source of truth for both the pill and the bubble. Mirrors backend semantics.

### 2. Floating pill (`ChatPane.tsx`)
- Computed reactively: `pmAgentOptIn(input).opted` — no extra state.
- Rendered inside `.chat-input-wrap`, above the `<textarea>`, only when `opted && !busy`.
- Disappears automatically when the text no longer starts with `/pm-agent` (dismiss = "delete the text").
- Content: `⚡ pm-agent · Redmine` (label from i18n).

### 3. Send path (`handleSend`)
```ts
const { opted, cleaned } = pmAgentOptIn(text);
setMessages(m => [...m, { role: "user", text: opted ? cleaned : text, pmAgent: opted }]);
// still send the FULL text — backend strips:
res = await api.chat.sendStream(sid, text, onStep, ctrl.signal);
```

### 4. User-bubble render
- `ThreadMsg` gains `pmAgent?: boolean` (persisted via existing localStorage serialization).
- When `m.role === "user" && m.pmAgent`: render a small `⚡ pm-agent` chip before `m.text`
  (the text already has the command stripped).

### 5. i18n (`src/i18n.ts`)
- `chat.pmAgentChip` = `"pm-agent"` (VI + EN).
- Pill suffix label reuses an existing/added key for "Redmine".

### 6. CSS
- `.pm-agent-pill` (floating pill) and `.pm-chip` (in-bubble chip), reusing existing accent
  color tokens. Locate the stylesheet that defines `.prompt-chip` / `.chat-input-wrap` and add
  there.

## Testing
- Unit test for `pmAgentOptIn`: lowercase, uppercase, leading whitespace, no-match,
  `/pm-agentXYZ` (no separator), empty string.
- **Caveat:** repo has no FE test harness yet (CLAUDE.md: `tests/` covers legacy `whisper_live`).
  Check `meeting_frontend_react/package.json` for vitest; if absent, note it and rely on
  `tsc --noEmit` + `npm run build`. Do not silently skip — report the gap.

## Out of scope
- No `X` button on the chip (dismiss = clear the text).
- No backend changes.
- No discoverability/autocomplete dropdown for slash commands.
