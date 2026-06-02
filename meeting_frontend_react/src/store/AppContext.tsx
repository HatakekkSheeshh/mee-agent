// Global app state: which meeting + recording is currently selected.
// Kept minimal — useState. Move to useReducer/zustand later if it grows.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "../api/client";
import type { Meeting, MeetingDetail, MoMJson, ProjectSummary } from "../types/api";
import { STRINGS, type Lang, type StringKey } from "../i18n";
import { ConfirmDialog, type ConfirmOpts } from "../components/ConfirmDialog";

type Theme = "dark" | "light";
export type PaneStatus = { kind: "info" | "assessing" | "success" | "error"; msg: string } | null;

interface AppState {
  meetings: Meeting[];
  meetingsLoading: boolean;
  currentMeetingId: string | null;
  currentMeeting: MeetingDetail | null;
  currentRecordingId: string | null;

  reloadMeetings: () => Promise<void>;
  selectMeeting: (id: string | null) => Promise<void>;
  selectRecording: (id: string | null) => void;
  reloadCurrentMeeting: () => Promise<void>;

  // UI prefs (persist to localStorage)
  theme: Theme;
  setTheme: (t: Theme) => void;
  chatOpen: boolean;
  toggleChat: () => void;
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: StringKey) => string;

  // Per-pane status banners — split so MoM-related ops show in MoMPane
  // (not the pane the button lives in).
  momStatus: PaneStatus;
  setMomStatus: (s: PaneStatus) => void;
  transcriptStatus: PaneStatus;
  setTranscriptStatus: (s: PaneStatus) => void;

  // Fresh-MoM cache — populated directly from generate-mom/summary responses.
  // MoMPane prefers this over currentMeeting.recordings[].mom_json so display
  // is instant + survives stale backend responses.
  freshRecordingMoms: Record<string, MoMJson>;
  setRecordingMom: (recordingId: string, mom: MoMJson) => void;
  freshProjectSummary: Record<string, ProjectSummary>;
  setProjectSummary: (meetingId: string, summary: ProjectSummary) => void;

  /** Async confirm — returns true if user clicked confirm, false otherwise. */
  confirm: (opts: ConfirmOpts) => Promise<boolean>;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [meetingsLoading, setMeetingsLoading] = useState(false);
  const [currentMeetingId, setCurrentMeetingId] = useState<string | null>(null);
  const [currentMeeting, setCurrentMeeting] = useState<MeetingDetail | null>(null);
  const [currentRecordingId, setCurrentRecordingId] = useState<string | null>(null);

  const reloadMeetings = useCallback(async () => {
    setMeetingsLoading(true);
    try {
      const list = await api.meetings.list();
      setMeetings(list);
    } catch (e) {
      console.error("Failed to load meetings:", e);
    } finally {
      setMeetingsLoading(false);
    }
  }, []);

  const reloadCurrentMeeting = useCallback(async () => {
    if (!currentMeetingId) {
      setCurrentMeeting(null);
      return;
    }
    try {
      const detail = await api.meetings.get(currentMeetingId);
      setCurrentMeeting(detail);
    } catch (e) {
      console.error("Failed to load meeting detail:", e);
    }
  }, [currentMeetingId]);

  const selectMeeting = useCallback(async (id: string | null) => {
    setCurrentMeetingId(id);
    setCurrentRecordingId(null);
    if (!id) {
      setCurrentMeeting(null);
      return;
    }
    try {
      const detail = await api.meetings.get(id);
      setCurrentMeeting(detail);
    } catch (e) {
      console.error("Failed to load meeting detail:", e);
    }
  }, []);

  const selectRecording = useCallback((id: string | null) => {
    setCurrentRecordingId(id);
  }, []);

  useEffect(() => {
    reloadMeetings();
  }, [reloadMeetings]);

  // ─── UI prefs ───
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem("mee.theme") as Theme) || "dark",
  );
  const setTheme = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("mee.theme", t);
    setThemeState(t);
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const [chatOpen, setChatOpen] = useState<boolean>(
    () => localStorage.getItem("mee.chatOpen") !== "false",
  );
  const toggleChat = useCallback(() => {
    setChatOpen((v) => {
      localStorage.setItem("mee.chatOpen", String(!v));
      return !v;
    });
  }, []);

  const [sidebarOpen, setSidebarOpen] = useState<boolean>(
    () => localStorage.getItem("mee.sidebarOpen") !== "0",
  );
  const toggleSidebar = useCallback(() => {
    setSidebarOpen((v) => {
      localStorage.setItem("mee.sidebarOpen", v ? "0" : "1");
      return !v;
    });
  }, []);
  useEffect(() => {
    document.body.classList.toggle("sidebar-open", sidebarOpen);
    document.body.classList.toggle("sidebar-collapsed", !sidebarOpen);
  }, [sidebarOpen]);

  const [lang, setLangState] = useState<Lang>(
    () => (localStorage.getItem("mee.lang") as Lang) || "vi",
  );
  const setLang = useCallback((l: Lang) => {
    localStorage.setItem("mee.lang", l);
    document.documentElement.setAttribute("lang", l);
    setLangState(l);
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("lang", lang);
  }, [lang]);
  const t = useCallback(
    (key: StringKey) => (STRINGS[lang] as Record<string, string>)[key] || key,
    [lang],
  );

  const [momStatus, setMomStatus] = useState<PaneStatus>(null);
  const [transcriptStatus, setTranscriptStatus] = useState<PaneStatus>(null);

  const [freshRecordingMoms, setFreshRecordingMoms] = useState<Record<string, MoMJson>>({});
  const setRecordingMom = useCallback((rid: string, mom: MoMJson) => {
    setFreshRecordingMoms((prev) => ({ ...prev, [rid]: mom }));
  }, []);
  const [freshProjectSummary, setFreshProjectSummary] = useState<Record<string, ProjectSummary>>({});
  const setProjectSummary = useCallback((mid: string, summary: ProjectSummary) => {
    setFreshProjectSummary((prev) => ({ ...prev, [mid]: summary }));
  }, []);

  // Async confirm — caller awaits the promise; resolved to true/false based on
  // which button user clicks. Replaces window.confirm() with branded modal.
  const [confirmState, setConfirmState] = useState<
    (ConfirmOpts & { resolve: (v: boolean) => void }) | null
  >(null);
  const confirm = useCallback(
    (opts: ConfirmOpts) =>
      new Promise<boolean>((resolve) => setConfirmState({ ...opts, resolve })),
    [],
  );

  const value: AppState = {
    meetings,
    meetingsLoading,
    currentMeetingId,
    currentMeeting,
    currentRecordingId,
    reloadMeetings,
    reloadCurrentMeeting,
    selectMeeting,
    selectRecording,
    theme,
    setTheme,
    chatOpen,
    toggleChat,
    sidebarOpen,
    toggleSidebar,
    lang,
    setLang,
    t,
    momStatus,
    setMomStatus,
    transcriptStatus,
    setTranscriptStatus,
    freshRecordingMoms,
    setRecordingMom,
    freshProjectSummary,
    setProjectSummary,
    confirm,
  };

  return (
    <AppContext.Provider value={value}>
      {children}
      <ConfirmDialog
        open={!!confirmState}
        title={confirmState?.title}
        message={confirmState?.message || ""}
        confirmLabel={confirmState?.confirmLabel}
        cancelLabel={confirmState?.cancelLabel}
        danger={confirmState?.danger}
        onConfirm={() => {
          confirmState?.resolve(true);
          setConfirmState(null);
        }}
        onCancel={() => {
          confirmState?.resolve(false);
          setConfirmState(null);
        }}
      />
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be inside <AppProvider>");
  return ctx;
}
