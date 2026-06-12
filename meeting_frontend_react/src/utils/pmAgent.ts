/**
 * Detect the `/pm-agent` explicit opt-in in a chat message.
 *
 * Mirrors the backend exactly (`meeting/graphs/chat_graph/classify.py::_pm_agent_opt_in`):
 * leading whitespace is ignored, the prefix match is case-insensitive, and NO separator
 * is required after the command (`/pm-agentXYZ` matches, cleaning to "XYZ"). Keep this in
 * sync with that backend helper — it is the single source of truth for both the floating
 * input pill and the in-bubble chip.
 */
export const PM_AGENT_COMMAND = "/pm-agent";

export interface PmAgentOptIn {
  /** True when the message starts with the /pm-agent command. */
  opted: boolean;
  /** The message with the command prefix stripped (unchanged when not opted in). */
  cleaned: string;
}

export function pmAgentOptIn(msg: string): PmAgentOptIn {
  const stripped = (msg ?? "").replace(/^\s+/, "");
  if (stripped.slice(0, PM_AGENT_COMMAND.length).toLowerCase() === PM_AGENT_COMMAND) {
    return {
      opted: true,
      cleaned: stripped.slice(PM_AGENT_COMMAND.length).replace(/^\s+/, ""),
    };
  }
  return { opted: false, cleaned: msg };
}
