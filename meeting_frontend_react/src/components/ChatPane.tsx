import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useApp } from "../store/AppContext";
import { api, ApiError } from "../api/client";
import type { ChatTurnResult, PendingAction } from "../types/api";
import { Markdown } from "./Markdown";
import { WelcomeBanner } from "./WelcomeBanner";

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
  // Free-text reply for a pm-agent need_more_info pause.
  const [infoInput, setInfoInput] = useState("");

  // The chat session id (LangGraph thread). Created lazily on first send and
  // re-created when the bound meeting changes. Kept in refs so it survives
  // re-renders without triggering them.
  const sessionIdRef = useRef<string | null>(null);
  const sessionMeetingRef = useRef<string | null>(null);

  // Persist the thread per meeting so it survives a page refresh (F5).
  const storageKey = `mee.chat.${currentMeetingId ?? "none"}`;

  // Restore on mount / when the bound meeting changes.
  useEffect(() => {
    sessionMeetingRef.current = currentMeetingId;
    try {
      const raw = localStorage.getItem(storageKey);
      const saved = raw
        ? (JSON.parse(raw) as {
            sessionId?: string | null;
            messages?: ThreadMsg[];
            pending?: PendingAction | null;
          })
        : null;
      sessionIdRef.current = saved?.sessionId ?? null;
      setMessages(saved?.messages ?? []);
      setPending(saved?.pending ?? null);
    } catch {
      sessionIdRef.current = null;
      setMessages([]);
      setPending(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentMeetingId]);

  // Save whenever the thread changes (sessionIdRef is set before any message,
  // so it is captured alongside the messages that triggered its creation).
  useEffect(() => {
    try {
      localStorage.setItem(
        storageKey,
        JSON.stringify({ sessionId: sessionIdRef.current, messages, pending }),
      );
    } catch {
      /* ignore quota / serialization errors */
    }
  }, [messages, pending, storageKey]);

  // Rotating example placeholder — surfaces what users can ask, including how
  // to reach the Redmine/pm-agent path. Cycles only while the box is empty/idle.
  const placeholderExamples = t("chat.examples").split("|").filter(Boolean);
  const [phIdx, setPhIdx] = useState(0);
  useEffect(() => {
    if (input || busy || placeholderExamples.length <= 1) return;
    const id = setInterval(
      () => setPhIdx((i) => (i + 1) % placeholderExamples.length),
      3500,
    );
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, busy, placeholderExamples.length]);

  // Auto-scroll the thread to the bottom whenever new content arrives.
  const threadRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending, busy]);

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

  // Submit the free-text answer to a pm-agent need_more_info pause: resume the
  // same task via /approve with {text} (backend maps it to the next message).
  const provideInfo = useCallback(async () => {
    if (!pending || busy) return;
    const text = infoInput.trim();
    if (!text) return;
    const id = pending.id;
    setPending(null);
    setInfoInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      applyResult(await api.chat.approve(id, { text }));
    } catch (e) {
      pushAgent(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [pending, busy, infoInput, t]);

  // Cancel a pm-agent need_more_info pause: pm-agent's need_more_info node
  // ends the thread on the literal text "/cancel" (NOT an approval-reject
  // DataPart), so send it as free text via /approve {text:"/cancel"}.
  const cancelInfo = useCallback(async () => {
    if (!pending || busy) return;
    const id = pending.id;
    setPending(null);
    setInfoInput("");
    setMessages((m) => [...m, { role: "user", text: "/cancel" }]);
    setBusy(true);
    try {
      applyResult(await api.chat.approve(id, { text: "/cancel" }));
    } catch (e) {
      pushAgent(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [pending, busy, t]);

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

      <div className="chat-thread" ref={threadRef}>
        <WelcomeBanner />

        {messages.length === 0 && (
          <div className="suggested-prompts">
            <button className="prompt-chip" type="button" onClick={() => setInput(t("agent.prompt1"))}>{t("agent.prompt1")}</button>
            <button className="prompt-chip" type="button" onClick={() => setInput(t("agent.prompt2"))}>{t("agent.prompt2")}</button>
            <button className="prompt-chip" type="button" onClick={() => setInput(t("agent.prompt3"))}>{t("agent.prompt3")}</button>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "msg msg-user" : "msg msg-agent"}>
            {m.role === "agent" ? <Markdown>{m.text}</Markdown> : m.text}
          </div>
        ))}

        {pending && pending.kind === "need_more_info" && (
          <div className="msg msg-agent pending-action">
            <div className="pending-title">{t("chat.needInfo")}</div>
            {/* {pending.task_id && (
              <div className="pending-thread small">Thread: {pending.task_id}</div>
            )} */}
            {pending.prompt && <Markdown>{pending.prompt}</Markdown>}
            <textarea
              className="chat-input pending-info-input"
              rows={2}
              placeholder={t("chat.infoPlaceholder")}
              value={infoInput}
              disabled={busy}
              onChange={(e) => setInfoInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void provideInfo();
                }
              }}
            />
            <div className="pending-buttons">
              <button className="btn btn-approve" type="button" disabled={busy || !infoInput.trim()} onClick={() => void provideInfo()}>
                {t("chat.send")}
              </button>
              <button className="btn btn-reject" type="button" disabled={busy} onClick={() => void cancelInfo()}>
                {t("chat.cancel")}
              </button>
            </div>
          </div>
        )}

        {pending && pending.kind !== "need_more_info" && (
          <div className="msg msg-agent pending-action">
            <div className="pending-title">
              {t("chat.pending")}: <strong>{pending.tool}</strong>
            </div>
            {/* {pending.task_id && (
              <div className="pending-thread small">Thread: {pending.task_id}</div>
            )} */}
            {pending.kind === "need_approval" && pending.issues?.length ? (
              <ul className="pending-issues">
                {pending.issues.map((iss, i) => (
                  <li key={i}>
                    {String(iss.actions ?? "")}{" "}
                    <strong>{String(iss.subject ?? JSON.stringify(iss))}</strong>
                  </li>
                ))}
              </ul>
            ) : (
              <pre className="pending-args">{JSON.stringify(pending.args, null, 2)}</pre>
            )}
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
          placeholder={placeholderExamples[phIdx] ?? t("chat.placeholder")}
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
