import { useEffect, useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { useResizer } from "../hooks/useResizer";
import { TranscriptPane } from "./TranscriptPane";
import { MoMPane } from "./MoMPane";
import { ChatPane } from "./ChatPane";
import { InsightsPane } from "./InsightsPane";
import { CommentsPane } from "./CommentsPane";
import { ProjectOverview } from "./ProjectOverview";

const MIN_LEFT = 280;
const DEFAULT_LEFT = 420;
const MIN_CHAT = 300;
const MAX_CHAT = 500;
const DEFAULT_CHAT = 380;

export function Workspace() {
  const { chatOpen, momOpen, insightsOpen, commentsOpen, currentMeeting, currentMeetingId, currentRecordingId, t } = useApp();
  const wsRef = useRef<HTMLDivElement>(null);

  const [leftW, setLeftW] = useState<number>(
    () => parseInt(localStorage.getItem("mee.mainLeftWidth") || `${DEFAULT_LEFT}`, 10),
  );
  const [chatW, setChatW] = useState<number>(
    () => parseInt(localStorage.getItem("mee.chatWidth") || `${DEFAULT_CHAT}`, 10),
  );

  const onMomMouseDown = useResizer({
    getStartValue: () => leftW,
    onChange: setLeftW,
    min: MIN_LEFT,
    max: () => window.innerWidth - 400 - (chatOpen ? chatW : 0),
    storageKey: "mee.mainLeftWidth",
  });

  const onChatMouseDown = useResizer({
    getStartValue: () => chatW,
    onChange: setChatW,
    min: MIN_CHAT,
    max: MAX_CHAT,
    invert: true,
    storageKey: "mee.chatWidth",
  });

  // Treat "no meeting selected" as if MoM pane were closed — the
  // transcript pane will be a full-width empty state CTA, so a MoM
  // strip beside it would just show "Chưa có biên bản" which is noise.
  const momVisible = momOpen && !!currentMeetingId;
  // Insights + Comments share the same slot as chat (only one open at
  // a time). When any of the 3 is open, reserve a right column same
  // width as chat. The "rightOpen" flag drives the grid layout.
  const rightOpen = chatOpen || insightsOpen || commentsOpen;
  useEffect(() => {
    const el = wsRef.current;
    if (!el) return;
    el.style.setProperty("--chat-width", `${chatW}px`);
    let cols: string;
    if (momVisible && rightOpen) cols = `${leftW}px 1px 1fr 1px ${chatW}px`;
    else if (momVisible) cols = `${leftW}px 1px 1fr`;
    else if (rightOpen) cols = `1fr 1px ${chatW}px`;
    else cols = `1fr`;
    el.style.gridTemplateColumns = cols;
  }, [leftW, chatW, rightOpen, momVisible]);

  function resetMom() { setLeftW(DEFAULT_LEFT); localStorage.removeItem("mee.mainLeftWidth"); }
  function resetChat() { setChatW(DEFAULT_CHAT); localStorage.setItem("mee.chatWidth", String(DEFAULT_CHAT)); }

  // Project-overview mode: project selected, no specific recording.
  // Apply body class so legacy CSS hides meta row + transcript chrome + MoM pane.
  const overviewMode =
    !!currentMeetingId && !currentRecordingId;
  useEffect(() => {
    document.body.classList.toggle("project-overview-mode", overviewMode);
    return () => { document.body.classList.remove("project-overview-mode"); };
  }, [overviewMode]);

  // In overview mode we still render Workspace structure (so panes are positioned
  // for the slide-back transition), but TranscriptPane shows the ProjectOverview
  // content instead of textarea. CSS hides .pane-mom + .pane-transcript chrome.
  const showOverview = overviewMode && (currentMeeting?.recordings.length || 0) > 0;

  return (
    <div
      ref={wsRef}
      className={`workspace${rightOpen ? " chat-open" : ""}${momVisible ? " mom-open" : " mom-closed"}`}
      id="workspace"
    >
      <TranscriptPane overviewContent={showOverview ? <ProjectOverview /> : null} />
      {momVisible && (
        <>
          <div
            className="resizer"
            id="resizer-mom"
            onMouseDown={onMomMouseDown}
            onDoubleClick={resetMom}
            title={t("workspace.resizerTitle")}
          />
          <MoMPane />
        </>
      )}
      {rightOpen && (
        <>
          <div
            className="resizer chat-resizer"
            id="resizer-chat"
            onMouseDown={onChatMouseDown}
            onDoubleClick={resetChat}
            title={t("workspace.resizerTitle")}
          />
          {chatOpen && <ChatPane />}
          {insightsOpen && <InsightsPane />}
          {commentsOpen && <CommentsPane />}
        </>
      )}
    </div>
  );
}
