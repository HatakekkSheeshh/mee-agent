import { useState } from "react";
import { useApp } from "../store/AppContext";

/** Arg keys always rendered as a textarea regardless of current length. */
const LONG_KEYS = ["body", "description", "content", "text"];
const LONG_VALUE_THRESHOLD = 80;

interface ActionArgsCardProps {
  tool: string;
  args: Record<string, unknown>;
  busy: boolean;
  onApprove: (edited: Record<string, unknown>) => void;
  onReject: () => void;
}

/**
 * Generic editable HITL card for local side-effect tools without a bespoke
 * card (everything except create_task / pm-agent kinds). Each string arg is
 * an editable field — the edits travel as `edited_args`, which the backend
 * merges before executing the tool (agent_execute). Non-string args render
 * read-only; schema-less by design so new tools get an editing UX for free.
 */
export function ActionArgsCard({ tool, args, busy, onApprove, onReject }: ActionArgsCardProps) {
  const { t } = useApp();
  const [draft, setDraft] = useState<Record<string, unknown>>(() => ({ ...args }));

  const setField = (key: string, value: string) =>
    setDraft((d) => ({ ...d, [key]: value }));

  return (
    <div className="msg msg-agent pending-action">
      <div className="pending-title">
        {t("chat.pending")}: <strong>{tool}</strong>
      </div>
      <div className="action-args">
        {Object.entries(draft).map(([key, value]) =>
          typeof value === "string" ? (
            <label key={key} className="task-field">
              <span className="task-label">{key}</span>
              {LONG_KEYS.includes(key) || value.length > LONG_VALUE_THRESHOLD ? (
                <textarea
                  className="chat-input action-arg-input"
                  rows={4}
                  value={value}
                  disabled={busy}
                  onChange={(e) => setField(key, e.target.value)}
                />
              ) : (
                <input
                  className="chat-input action-arg-input"
                  value={value}
                  disabled={busy}
                  onChange={(e) => setField(key, e.target.value)}
                />
              )}
            </label>
          ) : (
            <label key={key} className="task-field">
              <span className="task-label">{key}</span>
              <pre className="pending-args">{JSON.stringify(value, null, 2)}</pre>
            </label>
          ),
        )}
      </div>
      <div className="pending-buttons">
        <button
          className="btn btn-approve"
          type="button"
          disabled={busy}
          onClick={() => onApprove(draft)}
        >
          {t("chat.approve")}
        </button>
        <button className="btn btn-reject" type="button" disabled={busy} onClick={onReject}>
          {t("chat.reject")}
        </button>
      </div>
    </div>
  );
}
