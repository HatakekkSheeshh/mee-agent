// Floating action rail — vertical strip of round icon buttons anchored
// to the right edge of the workspace (Notta-style). Four buttons:
//   1. Chat with AI agent — pill with "Mee AI" label when closed, icon-
//      only when open so it doesn't compete with the open chat pane.
//   2. Biên bản báo cáo — toggles MoM pane. First click (no MoM yet)
//      also fires the generate-MoM action. Subsequent clicks just
//      toggle visibility; the MoM pane itself owns the Re-generate
//      action.
//   3. Comment (placeholder — TODO)
//   4. Conversation ratio / insights (placeholder — TODO)
import { useApp } from "../store/AppContext";

interface Props {
  /** Called when the user clicks the MoM rail button AND no MoM has
   * been generated yet. Lets the parent (TranscriptPane) own the actual
   * generate-MoM action while this component owns the toggle logic. */
  onGenerateMom?: () => void;
  /** Whether the current recording already has a MoM. Drives the
   * "first click also generates" behaviour. */
  hasMom?: boolean;
}

export function FloatingRail({ onGenerateMom, hasMom = false }: Props) {
  const {
    chatOpen,
    toggleChat,
    momOpen,
    setMomOpen,
    insightsOpen,
    toggleInsights,
    commentsOpen,
    toggleComments,
    t,
  } = useApp();

  function handleMomClick() {
    if (!momOpen) {
      // Opening the pane. If no MoM yet, also fire the generate action
      // so the user goes from "nothing" → "MoM appearing" in one click.
      setMomOpen(true);
      if (!hasMom) onGenerateMom?.();
    } else {
      // Already open → close. The MoM pane itself provides the
      // Re-generate button for refreshing content.
      setMomOpen(false);
    }
  }

  // toggleInsights / toggleComments in context auto-close the OTHER
  // of the two; we also need to ensure chat closes when opening one
  // of them (and vice-versa) so the right-side slot only has one
  // occupant. Wrap each toggle to enforce that.
  function handleChat() {
    if (!chatOpen) {
      // Opening chat — close insights/comments if open.
      if (insightsOpen) toggleInsights();
      if (commentsOpen) toggleComments();
    }
    toggleChat();
  }
  function handleInsights() {
    if (!insightsOpen && chatOpen) toggleChat();
    toggleInsights();
  }
  function handleComments() {
    if (!commentsOpen && chatOpen) toggleChat();
    toggleComments();
  }

  return (
    <div className="floating-rail" aria-label="Quick actions">
      <button
        type="button"
        className={`rail-btn rail-btn-ai${chatOpen ? " active icon-only" : " pill"}`}
        title={t("rail.chat")}
        onClick={handleChat}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
          <circle cx="12" cy="12" r="4" />
        </svg>
        {!chatOpen && <span className="rail-btn-label">{t("rail.brand")}</span>}
      </button>

      <button
        type="button"
        className={`rail-btn${momOpen ? " active" : ""}`}
        title={momOpen ? t("rail.mom.hide") : (hasMom ? t("rail.mom.show") : t("rail.mom.generate"))}
        onClick={handleMomClick}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          {/* Document icon — sheet of paper with folded corner + 3 text lines */}
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="8" y1="13" x2="16" y2="13" />
          <line x1="8" y1="17" x2="16" y2="17" />
          <line x1="8" y1="9" x2="10" y2="9" />
        </svg>
      </button>

      <button
        type="button"
        className={`rail-btn${commentsOpen ? " active" : ""}`}
        title={t("rail.comment")}
        onClick={handleComments}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </button>

      <button
        type="button"
        className={`rail-btn${insightsOpen ? " active" : ""}`}
        title={t("rail.insights")}
        onClick={handleInsights}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12a9 9 0 1 1-9-9" />
          <path d="M21 12A9 9 0 0 0 12 3v9z" fill="currentColor" fillOpacity="0.15" />
        </svg>
      </button>
    </div>
  );
}
