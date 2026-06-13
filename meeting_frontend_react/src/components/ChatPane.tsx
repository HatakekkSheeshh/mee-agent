import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useApp } from "../store/AppContext";
import { toolLabel as mapToolLabel } from "../i18n";
import { api, ApiError } from "../api/client";
import type { ChatStreamStep, ChatTurnResult, PendingAction } from "../types/api";
import { Markdown } from "./Markdown";
import { WelcomeBanner } from "./WelcomeBanner";
import { ActionArgsCard } from "./ActionArgsCard";
import { CreateTaskCard, parseTaskTemplate, type TaskTemplate } from "./CreateTaskCard";
import { pmAgentOptIn } from "../utils/pmAgent";
import { slashMatches, type SlashCommand } from "../utils/slashCommands";
import { ChatInput, type ChatInputHandle } from "./ChatInput";

interface ThreadMsg {
  role: "user" | "agent" | "note" | "card";
  text: string;
  /** Activity-trace labels collected while the turn streamed (agent msgs only). */
  steps?: string[];
  /** User msg sent via the /pm-agent command — render a chip and show stripped text. */
  pmAgent?: boolean;
  /** Read-only snapshot of a resolved pending action (role === "card"). */
  card?: PendingAction;
  cardStatus?: "approved" | "rejected" | "sent";
}

export function ChatPane() {
  const { t, currentMeeting, currentMeetingId, confirm } = useApp();
  const [messages, setMessages] = useState<ThreadMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // Slash-command menu: highlighted row + an Escape-dismiss flag (reset on edit).
  const [slashIdx, setSlashIdx] = useState(0);
  const [slashClosed, setSlashClosed] = useState(false);
  const inputRef = useRef<ChatInputHandle>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  // Free-text reply for a pm-agent need_more_info pause.
  const [infoInput, setInfoInput] = useState("");
  // Live activity trace for the in-flight streamed turn.
  const [steps, setSteps] = useState<string[]>([]);
  // Abort handle for the in-flight streamed send (the stop button).
  const abortRef = useRef<AbortController | null>(null);
  // Index of the agent message whose copy button just fired ("copied" flash).
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  // Whether the current pending-action card is zoomed into the modal overlay.
  // One flag suffices — at most one pending card exists at a time.
  const [zoomed, setZoomed] = useState(false);
  useEffect(() => setZoomed(false), [pending?.id]);

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

  const pushNote = (text: string) =>
    setMessages((m) => [...m, { role: "note", text }]);

  const applyResult = (res: ChatTurnResult, traceSteps?: string[]) => {
    if (res.status === "interrupted") {
      setPending(res.pending_action);
      // Only the LLM's own rationale is user-facing; the tool spec
      // `description` is internal English prompt text — never show it.
      const hint = res.pending_action.rationale;
      if (hint) pushAgent(hint);
    } else {
      setPending(null);
      setMessages((m) => [
        ...m,
        {
          role: "agent",
          text: res.reply,
          ...(traceSteps?.length ? { steps: traceSteps } : {}),
        },
      ]);
    }
  };

  // Tool name → localized label (shared i18n helper, bound to this t).
  const toolLabel = useCallback(
    (name: string): string => mapToolLabel(t, name),
    [t],
  );

  // SSE step event → localized trace label.
  const stepLabel = useCallback(
    (ev: ChatStreamStep): string | null => {
      if (ev.step === "context") return t("chat.step.context");
      if (ev.step === "classify") return t("chat.step.classify");
      if (ev.step === "pm") return t("chat.step.pm");
      if (ev.step === "tool_call") {
        const names = (ev.tools ?? []).map(toolLabel);
        return `${t("chat.step.tool")} ${names.join(", ")}`;
      }
      return null; // tool_done — completion shows on the next step/answer
    },
    [t, toolLabel],
  );

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
    // Mirror the backend /pm-agent opt-in for display: show a chip + the command-stripped
    // text in the bubble, but still send the FULL text — the backend strips it itself.
    const { opted: pmAgent, cleaned } = pmAgentOptIn(text);
    setMessages((m) => [...m, { role: "user", text: pmAgent ? cleaned : text, pmAgent }]);
    setBusy(true);
    setSteps([]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // Collected outside state so the final message gets the full trace even
    // though setSteps batches.
    const collected: string[] = [];
    const onStep = (ev: ChatStreamStep) => {
      const label = stepLabel(ev);
      if (!label) return;
      collected.push(label);
      setSteps((s) => [...s, label]);
    };
    try {
      const sid = await ensureSession();
      let res: ChatTurnResult;
      try {
        res = await api.chat.sendStream(sid, text, onStep, ctrl.signal);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") throw e;
        // Stream route missing (older backend) → fall back to the blocking POST.
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
          res = await api.chat.send(sid, text);
        } else {
          throw e;
        }
      }
      applyResult(res, collected);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        pushNote(t("chat.stopped"));
      } else {
        pushAgent(errorText(e));
      }
    } finally {
      abortRef.current = null;
      setSteps([]);
      setBusy(false);
    }
  }, [input, busy, ensureSession, stepLabel, t]);

  // Clear the session in place: wipe its messages + pending + checkpoint on the
  // backend (keeping the session id / meeting binding), then empty the local
  // thread and re-show the welcome banner. The save effect persists the cleared
  // thread (sessionId kept), so the localStorage cache resets too.
  const handleClear = useCallback(async () => {
    if (busy) return;
    const ok = await confirm({
      title: t("chat.clear"),
      message: t("chat.clearConfirm"),
      confirmLabel: t("chat.clear"),
      cancelLabel: t("confirm.cancel"),
      accent: true,
    });
    if (!ok) return;
    const sid = sessionIdRef.current;
    setBusy(true);
    try {
      if (sid) await api.chat.clear(sid);
      setMessages([]);
      setPending(null);
    } catch (e) {
      pushAgent(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [busy, t, confirm]);

  // Keep a read-only snapshot of a resolved pending card in the thread, so the
  // conversation history preserves what was approved / sent / rejected.
  const archiveCard = useCallback(
    (action: PendingAction, status: "approved" | "rejected" | "sent") =>
      setMessages((m) => [...m, { role: "card", text: "", card: action, cardStatus: status }]),
    [],
  );

  const decide = useCallback(
    async (approve: boolean) => {
      if (!pending || busy) return;
      const id = pending.id;
      const snapshot = pending;
      setPending(null);
      // Keep the resolved card visible in the thread history.
      archiveCard(snapshot, approve ? "approved" : "rejected");
      setBusy(true);
      try {
        applyResult(approve ? await api.chat.approve(id) : await api.chat.reject(id));
      } catch (e) {
        pushAgent(errorText(e));
      } finally {
        setBusy(false);
      }
    },
    [pending, busy, t, toolLabel],
  );

  // Approve a generic local side-effect tool with the user's field edits
  // (ActionArgsCard). The backend merges `edited_args` before executing.
  const approveGeneric = useCallback(
    async (edited: Record<string, unknown>) => {
      if (!pending || busy) return;
      const id = pending.id;
      const snapshot = pending;
      setPending(null);
      archiveCard({ ...snapshot, args: { ...snapshot.args, ...edited } }, "approved");
      setBusy(true);
      try {
        applyResult(await api.chat.approve(id, { edited_args: edited }));
      } catch (e) {
        pushAgent(errorText(e));
      } finally {
        setBusy(false);
      }
    },
    [pending, busy, t],
  );

  const copyMsg = useCallback(async (idx: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx((c) => (c === idx ? null : c)), 1500);
    } catch {
      /* clipboard unavailable (http origin) — silently skip */
    }
  }, []);

  // Approve the create_task GATE-1 card with the user's edits. The edited
  // template is sent as `edited_args`; the backend merges it into the reconcile
  // payload before handing off to pm-agent (GATE 2).
  const approveCreateTask = useCallback(
    async (edited: TaskTemplate, reason: string) => {
      if (!pending || busy) return;
      const id = pending.id;
      const snapshot = pending;
      setPending(null);
      archiveCard({ ...snapshot, args: { project: edited.project, items: edited.items } }, "sent");
      setBusy(true);
      try {
        applyResult(
          await api.chat.approve(id, {
            edited_args: { project: edited.project, items: edited.items },
            ...(reason ? { reason } : {}),
          }),
        );
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
    const snapshot = pending;
    setPending(null);
    setInfoInput("");
    archiveCard(snapshot, "sent");
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
    const snapshot = pending;
    setPending(null);
    setInfoInput("");
    archiveCard(snapshot, "rejected");
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

  // Slash-command menu state, derived from the input. Shown only while the user
  // is still typing the command token; once committed, the /pm-agent prefix is
  // highlighted inline inside the input itself (see ChatInput).
  const slashList = slashClosed || busy ? null : slashMatches(input);
  const showSlashMenu = !!slashList;
  // Reset the highlight as the filtered list changes with each keystroke.
  useEffect(() => setSlashIdx(0), [input]);

  const acceptSlash = useCallback((cmd: SlashCommand) => {
    setInput(cmd.command + " ");
    setSlashClosed(true);
    inputRef.current?.focus();
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (showSlashMenu && slashList) {
      const n = slashList.length;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIdx((i) => (i + 1) % n);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIdx((i) => (i - 1 + n) % n);
        return;
      }
      if (e.key === "Tab" || e.key === "Enter") {
        e.preventDefault();
        acceptSlash(slashList[Math.min(slashIdx, n - 1)]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashClosed(true);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  // GATE 1 for create_task carries an editable {project, items} template
  // (tool === "create_task", no pm `kind`). Null for every other pending action.
  const taskTemplate =
    pending && pending.tool === "create_task" && !pending.kind
      ? parseTaskTemplate(pending.args)
      : null;

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
          {(messages.length > 0 || pending) && (
            <button
              className="icon-btn icon-btn-sm"
              type="button"
              title={t("chat.clear")}
              aria-label={t("chat.clear")}
              disabled={busy}
              onClick={() => void handleClear()}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          )}
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

        {messages.map((m, i) =>
          m.role === "note" ? (
            <div key={i} className="msg msg-note">{m.text}</div>
          ) : m.role === "card" && m.card ? (
            <ArchivedCard key={i} card={m.card} status={m.cardStatus ?? "approved"} />
          ) : (
            <div key={i} className={m.role === "user" ? "msg msg-user" : "msg msg-agent"}>
              {m.role === "agent" && m.steps?.length ? (
                <details className="chat-activity chat-activity-collapsed">
                  <summary className="small">
                    {t("chat.stepsSummary", { n: m.steps.length })}
                  </summary>
                  {m.steps.map((s, j) => (
                    <div key={j} className="activity-step activity-done">
                      <span className="activity-check">✓</span> {s}
                    </div>
                  ))}
                </details>
              ) : null}
              {m.role === "agent" ? (
                <Markdown>{m.text}</Markdown>
              ) : (
                <>
                  {m.pmAgent && (
                    <span className="pm-chip">
                      <img className="pm-chip-ava" src="/pm-agent-ava.webp" alt="" />
                      {t("chat.pmAgentChip")}
                    </span>
                  )}
                  {m.text}
                </>
              )}
              {m.role === "agent" && m.text && (
                <button
                  className="msg-copy"
                  type="button"
                  title={copiedIdx === i ? t("chat.copied") : t("chat.copy")}
                  aria-label={t("chat.copy")}
                  onClick={() => void copyMsg(i, m.text)}
                >
                  {copiedIdx === i ? (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                    </svg>
                  )}
                </button>
              )}
            </div>
          ),
        )}

        {pending && pending.kind === "need_more_info" && (
          <ZoomCard zoomed={zoomed} onZoom={setZoomed}>
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
          </ZoomCard>
        )}

        {taskTemplate && (
          <ZoomCard zoomed={zoomed} onZoom={setZoomed}>
            <CreateTaskCard
              template={taskTemplate}
              busy={busy}
              onApprove={(edited, reason) => void approveCreateTask(edited, reason)}
              onReject={() => void decide(false)}
            />
          </ZoomCard>
        )}

        {pending && pending.kind === "pm_error" && (
          <ZoomCard zoomed={zoomed} onZoom={setZoomed}>
          <div className="msg msg-agent pending-action">
            <div className="pending-title">{t("chat.pmError.title")}</div>
            {pending.prompt && <Markdown>{pending.prompt}</Markdown>}
            <div className="pending-buttons">
              <button className="btn btn-approve" type="button" disabled={busy} onClick={() => void decide(true)}>
                {t("chat.pmError.retry")}
              </button>
              <button className="btn btn-reject" type="button" disabled={busy} onClick={() => void decide(false)}>
                {t("chat.cancel")}
              </button>
            </div>
          </div>
          </ZoomCard>
        )}

        {/* Local side-effect tool without a bespoke card → generic editable card. */}
        {pending && !pending.kind && !taskTemplate && (
          <ZoomCard zoomed={zoomed} onZoom={setZoomed}>
            <ActionArgsCard
              key={pending.id}
              tool={pending.tool}
              args={pending.args}
              busy={busy}
              onApprove={(edited) => void approveGeneric(edited)}
              onReject={() => void decide(false)}
            />
          </ZoomCard>
        )}

        {/* pm-agent need_approval — issues list straight from pm-agent. */}
        {pending && pending.kind === "need_approval" && (
          <ZoomCard zoomed={zoomed} onZoom={setZoomed}>
          <div className="msg msg-agent pending-action">
            <div className="pending-title">
              {t("chat.pending")}: <strong>{toolLabel(pending.tool)}</strong>
            </div>
            {pending.issues?.length ? (
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
          </ZoomCard>
        )}

        {busy && (
          <div className="msg msg-agent msg-typing">
            {steps.length > 0 ? (
              <div className="chat-activity">
                {steps.map((s, i) => (
                  <div
                    key={i}
                    className={
                      "activity-step " +
                      (i === steps.length - 1 ? "activity-live" : "activity-done")
                    }
                  >
                    {i === steps.length - 1 ? (
                      <span className="activity-spinner" />
                    ) : (
                      <span className="activity-check">✓</span>
                    )}{" "}
                    {s}
                  </div>
                ))}
              </div>
            ) : (
              t("chat.thinking")
            )}
          </div>
        )}
      </div>

      <div className="chat-input-wrap">
        {showSlashMenu && slashList && (
          <div className="slash-menu" role="listbox" aria-label={t("chat.slash.menuLabel")}>
            {slashList.map((c, i) => (
              <button
                key={c.command}
                type="button"
                role="option"
                aria-selected={i === slashIdx}
                className={`slash-item${i === slashIdx ? " slash-item-active" : ""}`}
                onMouseEnter={() => setSlashIdx(i)}
                onMouseDown={(e) => {
                  e.preventDefault(); // keep textarea focus
                  acceptSlash(c);
                }}
              >
                {c.icon && <img className="slash-ava" src={c.icon} alt="" />}
                <span className="slash-cmd">{c.command}</span>
                <span className="slash-desc">{t(c.descKey)}</span>
              </button>
            ))}
          </div>
        )}
        <ChatInput
          ref={inputRef}
          value={input}
          placeholder={placeholderExamples[phIdx] ?? t("chat.placeholder")}
          ariaLabel={t("chat.placeholder")}
          disabled={busy}
          onChange={(v) => {
            setInput(v);
            setSlashClosed(false);
          }}
          onKeyDown={onKeyDown}
        />
        {busy ? (
          <button
            className="chat-send chat-stop"
            type="button"
            title={t("chat.stop")}
            aria-label={t("chat.stop")}
            disabled={!abortRef.current}
            onClick={() => abortRef.current?.abort()}
          >
            <svg viewBox="0 0 24 24" fill="currentColor">
              <rect x="7" y="7" width="10" height="10" rx="1.5" />
            </svg>
          </button>
        ) : (
          <button
            className="chat-send"
            type="button"
            title="Send"
            disabled={!input.trim()}
            onClick={() => void handleSend()}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
        )}
      </div>
    </section>
  );
}

function ArchivedCard({ card, status }: { card: PendingAction; status: string }) {
  const { t } = useApp();
  const items = (card.args?.items as Array<Record<string, unknown>> | undefined) ?? undefined;
  const badge =
    status === "rejected"
      ? `✕ ${t("chat.card.rejected")}`
      : status === "sent"
        ? `↗ ${t("chat.card.sent")}`
        : `✓ ${t("chat.card.approved")}`;
  return (
    <div className={`msg msg-agent pending-action card-archived card-${status}`}>
      <div className="pending-title">
        {mapToolLabel(t, card.tool)} <span className="card-badge">{badge}</span>
      </div>
      {card.prompt && <Markdown>{card.prompt}</Markdown>}
      {card.issues?.length ? (
        <ul className="pending-issues">
          {card.issues.map((iss, i) => (
            <li key={i}>
              {String(iss.actions ?? "")}{" "}
              <strong>{String(iss.subject ?? JSON.stringify(iss))}</strong>
            </li>
          ))}
        </ul>
      ) : items?.length ? (
        <ul className="pending-issues">
          {items.map((it, i) => (
            <li key={i}>
              <strong>{String(it.subject ?? it.title ?? JSON.stringify(it))}</strong>
            </li>
          ))}
        </ul>
      ) : !card.prompt && card.args ? (
        <pre className="pending-args">{JSON.stringify(card.args, null, 2)}</pre>
      ) : null}
    </div>
  );
}

interface ZoomCardProps {
  zoomed: boolean;
  onZoom: (z: boolean) => void;
  children: ReactNode;
}

/**
 * Magnify wrapper for pending-action cards. Zoomed = the SAME card subtree is
 * styled into a centered fixed overlay (CSS only — no portal/remount, so
 * in-progress field edits survive toggling). Esc / backdrop click collapse.
 */
function ZoomCard({ zoomed, onZoom, children }: ZoomCardProps) {
  const { t } = useApp();
  useEffect(() => {
    if (!zoomed) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onZoom(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomed, onZoom]);

  return (
    <div className={zoomed ? "card-zoom card-zoom-open" : "card-zoom"}>
      {zoomed && <div className="card-zoom-backdrop" onClick={() => onZoom(false)} />}
      <div className="card-zoom-host">
        <button
          className="card-zoom-btn"
          type="button"
          title={zoomed ? t("chat.zoomOut") : t("chat.zoomIn")}
          aria-label={zoomed ? t("chat.zoomOut") : t("chat.zoomIn")}
          onClick={() => onZoom(!zoomed)}
        >
          {zoomed ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="4 14 10 14 10 20" />
              <polyline points="20 10 14 10 14 4" />
              <line x1="14" y1="10" x2="21" y2="3" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          )}
        </button>
        {children}
      </div>
    </div>
  );
}
