import { useApp } from "../store/AppContext";

export function ChatPane() {
  const { t, currentMeeting } = useApp();
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
        <div className="suggested-prompts">
          <button className="prompt-chip" type="button">{t("agent.prompt1")}</button>
          <button className="prompt-chip" type="button">{t("agent.prompt2")}</button>
          <button className="prompt-chip" type="button">{t("agent.prompt3")}</button>
        </div>
      </div>

      <div className="chat-input-wrap">
        <textarea
          className="chat-input"
          rows={1}
          placeholder={t("chat.placeholder")}
        />
        <button className="chat-send" type="button" title="Send">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="19" x2="12" y2="5" />
            <polyline points="5 12 12 5 19 12" />
          </svg>
        </button>
      </div>
    </section>
  );
}
