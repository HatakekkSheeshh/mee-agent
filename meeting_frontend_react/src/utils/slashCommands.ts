/**
 * Slash-command registry for the chat input.
 *
 * Typing `/` at the start of the box opens a suggestion menu listing these commands;
 * the user picks one with ↑/↓ + Tab/Enter, or by clicking. Adding a command is a one-line
 * registry entry — keep the `command` string in sync with whatever the backend matches
 * (e.g. PM_AGENT_COMMAND in meeting/graphs/_chat_state.py).
 */
import { PM_AGENT_COMMAND } from "./pmAgent";
import type { StringKey } from "../i18n";

export interface SlashCommand {
  /** The literal command token, including the leading slash. */
  command: string;
  /** i18n key for the short label. */
  labelKey: StringKey;
  /** i18n key for the one-line description shown in the menu. */
  descKey: StringKey;
  /** Optional avatar served from public/ (absolute path). */
  icon?: string;
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {
    command: PM_AGENT_COMMAND,
    labelKey: "chat.pmAgentChip",
    descKey: "chat.slash.pmAgentDesc",
    icon: "/pm-agent-ava.webp",
  },
];

/**
 * Commands to suggest for the current input, or null when the menu should be closed.
 *
 * The menu is open only while the user is still typing the command token — input starts
 * with "/" and contains no whitespace yet. Once a space is typed (the command is committed
 * and arguments begin), this returns null so the floating pill can take over.
 */
export function slashMatches(input: string): SlashCommand[] | null {
  if (!input.startsWith("/")) return null;
  if (/\s/.test(input)) return null; // a space → command chosen, stop suggesting
  const q = input.toLowerCase();
  const matches = SLASH_COMMANDS.filter((c) => c.command.toLowerCase().startsWith(q));
  return matches.length ? matches : null;
}
