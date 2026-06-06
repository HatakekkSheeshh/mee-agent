import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { useApp } from "../store/AppContext";
import { api, ApiError } from "../api/client";
import type { ChatTurnResult, PendingAction } from "../types/api";

interface ThreadMsg {
  role: "user" | "agent";
  text: string;
}

export function ChatPane() {
  const { t, currentMeeting, currentMeetingId } = useApp();
  const [messages, setMessages] = useState<ThreadMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);

  // The chat session id (LangGraph thread). Created lazily on first send and
  // re-created when the bound meeting changes. Kept in refs so it survives
  // re-renders without triggering them.
  const sessionIdRef = useRef<string | null>(null);
  const sessionMeetingRef = useRef<string | null>(null);

  const pushAgent = (text: string) =>
    setMessages((m) => [...m, { role: "agent", text }]);

  const applyResult = (res: ChatTurnResult) => {
    if (res.status === "interrupted") {
      setPending(res.pending_action);
      const hint = res.pending_action.rationale || res.pending_action.description;
      if (hint) pushAgent(hint);
    } else {
      setPending(null);
      pushAgent(res.reply);
    }
  };

  const errorText = (e: unknown) =>
    `${t("chat.error")}: ${e instanceof ApiError ? e.detail : String(e)}`;

  const ensureSession = useCallback(async (): Promise<string> => {
    if (sessionIdRef.current && sessionMeetingRef.current === currentMeetingId) {
      return sessionIdRef.current;
    }
    const s = await api.chat.createSession(currentMeetingId ?? "");
    sessionIdRef.current = s.id;
    sessionMeetingRef.current = currentMeetingId;
    return s.id;
  }, [currentMeetingId]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const sid = await ensureSession();
      applyResult(await api.chat.send(sid, text));
    } catch (e) {
      pushAgent(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [input, busy, ensureSession, t]);

  const decide = useCallback(
    async (approve: boolean) => {
      if (!pending || busy) return;
      const id = pending.id;
      setPending(null);
      setBusy(true);
      try {
        applyResult(approve ? await api.chat.approve(id) : await api.chat.reject(id));
      } catch (e) {
        pushAgent(errorText(e));
      } finally {
        setBusy(false);
      }
    },
    [pending, busy, t],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  return (
    <section className="pane pane-chat">
      <div className="pane-header">
        <span className="pane-title">
          <span className="agent-dot"></span> {t("pane.agent")}
        </span>
        <div className="pane-meta">
          <span className="small">
            {currentMeeting ? currentMeeting.title : t("agent.noMeeting")}
          </span>
        </div>
      </div>

      <div className="chat-thread">
        <div className="msg msg-agent">
          {t("agent.welcome")}
          <div className="msg-meta">{t("agent.justNow")}</div>
        </div>

        {messages.length === 0 && (
          <div className="suggested-prompts">
            <button className="prompt-chip" type="button" onClick={() => setInput(t("agent.prompt1"))}>{t("agent.prompt1")}</button>
            <button className="prompt-chip" type="button" onClick={() => setInput(t("agent.prompt2"))}>{t("agent.prompt2")}</button>
            <button className="prompt-chip" type="button" onClick={() => setInput(t("agent.prompt3"))}>{t("agent.prompt3")}</button>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "msg msg-user" : "msg msg-agent"}>
            {m.text}
          </div>
        ))}

        {pending && (
          <div className="msg msg-agent pending-action">
            <div className="pending-title">
              {t("chat.pending")}: <strong>{pending.tool}</strong>
            </div>
            <pre className="pending-args">{JSON.stringify(pending.args, null, 2)}</pre>
            <div className="pending-buttons">
              <button className="btn btn-approve" type="button" disabled={busy} onClick={() => void decide(true)}>
                {t("chat.approve")}
              </button>
              <button className="btn btn-reject" type="button" disabled={busy} onClick={() => void decide(false)}>
                {t("chat.reject")}
              </button>
            </div>
          </div>
        )}

        {busy && <div className="msg msg-agent msg-typing">{t("chat.thinking")}</div>}
      </div>

      <div className="chat-input-wrap">
        <textarea
          className="chat-input"
          rows={1}
          placeholder={t("chat.placeholder")}
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          className="chat-send"
          type="button"
          title="Send"
          disabled={busy || !input.trim()}
          onClick={() => void handleSend()}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="19" x2="12" y2="5" />
            <polyline points="5 12 12 5 19 12" />
          </svg>
        </button>
      </div>
    </section>
  );
}
