import { useEffect, useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { useResizer } from "../hooks/useResizer";
import { TranscriptPane } from "./TranscriptPane";
import { MoMPane } from "./MoMPane";
import { ChatPane } from "./ChatPane";
import { ProjectOverview } from "./ProjectOverview";

const MIN_LEFT = 280;
const DEFAULT_LEFT = 420;
const MIN_CHAT = 300;
const MAX_CHAT = 500;
const DEFAULT_CHAT = 380;

export function Workspace() {
  const { chatOpen, currentMeeting, currentMeetingId, currentRecordingId } = useApp();
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

  useEffect(() => {
    const el = wsRef.current;
    if (!el) return;
    el.style.setProperty("--chat-width", `${chatW}px`);
    el.style.gridTemplateColumns = chatOpen
      ? `${leftW}px 1px 1fr 1px ${chatW}px`
      : `${leftW}px 1px 1fr`;
  }, [leftW, chatW, chatOpen]);

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
      className={`workspace${chatOpen ? " chat-open" : ""}`}
      id="workspace"
    >
      <TranscriptPane overviewContent={showOverview ? <ProjectOverview /> : null} />
      <div
        className="resizer"
        id="resizer-mom"
        onMouseDown={onMomMouseDown}
        onDoubleClick={resetMom}
        title="Kéo để thay đổi kích thước · double-click để reset"
      />
      <MoMPane />
      {chatOpen && (
        <>
          <div
            className="resizer chat-resizer"
            id="resizer-chat"
            onMouseDown={onChatMouseDown}
            onDoubleClick={resetChat}
            title="Kéo để thay đổi kích thước · double-click để reset"
          />
          <ChatPane />
        </>
      )}
    </div>
  );
}
