// Global app state: which meeting + recording is currently selected.
// Kept minimal — useState. Move to useReducer/zustand later if it grows.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
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
  momOpen: boolean;
  toggleMom: () => void;
  setMomOpen: (v: boolean) => void;
  detailsOpen: boolean;
  toggleDetails: () => void;
  insightsOpen: boolean;
  toggleInsights: () => void;
  commentsOpen: boolean;
  toggleComments: () => void;
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: StringKey, vars?: Record<string, string | number>) => string;
  // Audio device prefs — null = browser/OS default.
  audioInputDeviceId: string | null;
  setAudioInputDeviceId: (id: string | null) => void;
  audioOutputDeviceId: string | null;
  setAudioOutputDeviceId: (id: string | null) => void;
  audioDevices: { inputs: MediaDeviceInfo[]; outputs: MediaDeviceInfo[] };
  refreshAudioDevices: () => Promise<void>;

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

  // Per-recording generation tracking — lifted to context so the UI shows
  // "Đang tạo…" + disables the button even after user switches away and
  // comes back. Backend may still be running while FE forgot it locally.
  generatingRecordings: Set<string>;
  markGeneratingRecording: (recordingId: string) => void;
  unmarkGeneratingRecording: (recordingId: string) => void;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [meetingsLoading, setMeetingsLoading] = useState(false);
  const [currentMeetingId, setCurrentMeetingId] = useState<string | null>(null);
  const [currentMeeting, setCurrentMeeting] = useState<MeetingDetail | null>(null);
  const [currentRecordingId, setCurrentRecordingId] = useState<string | null>(null);

  // Set of recording ids whose MoM/clean is being generated in the background.
  // Survives recording-switch — when user comes back, UI re-shows "Đang tạo…".
  const [generatingRecordings, setGeneratingRecordings] = useState<Set<string>>(new Set());
  const markGeneratingRecording = useCallback((rid: string) => {
    setGeneratingRecordings((prev) => {
      const next = new Set(prev);
      next.add(rid);
      return next;
    });
  }, []);
  const unmarkGeneratingRecording = useCallback((rid: string) => {
    setGeneratingRecordings((prev) => {
      const next = new Set(prev);
      next.delete(rid);
      return next;
    });
  }, []);

  // Token to discard stale fetch responses when user switches meeting fast.
  // Without this, the response of meeting Y can arrive AFTER user switched
  // back to X, overwriting X's data with Y's. Each fetch bumps the token;
  // the response handler aborts if the token moved on.
  const meetingFetchTokenRef = useRef(0);

  // Always-fresh mirror of currentMeetingId. Updated SYNCHRONOUSLY by
  // selectMeeting (not via useEffect) because effects fire async after
  // render, leaving a microsecond gap where the ref is stale. A background
  // reloadCurrentMeeting() resolving inside that gap would read the old id
  // and fetch+commit the wrong project's data — exactly the bug where GIP's
  // view shows AI Innovation's recordings.
  const currentMeetingIdRef = useRef(currentMeetingId);

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
    // Read LATEST meeting id via ref, not the value captured when this
    // callback was first created. Background callers (handleGenerateMom
    // completion → reloadCurrentMeeting()) ran in a render when meetingId
    // was X; by the time gen finishes, user is on Y. We don't want to
    // refetch X and overwrite Y. If user has already switched away, skip
    // the reload entirely — the current meeting's data is unrelated.
    const targetId = currentMeetingIdRef.current;
    if (!targetId) {
      setCurrentMeeting(null);
      return;
    }
    const token = ++meetingFetchTokenRef.current;
    try {
      const detail = await api.meetings.get(targetId);
      // Triple-guard: token bumped → newer fetch in flight, ref changed →
      // user moved away, response id mismatch → backend returned wrong row.
      // Any of these means committing this data would corrupt the view.
      if (meetingFetchTokenRef.current !== token) return;
      if (currentMeetingIdRef.current !== targetId) return;
      if (detail.id !== currentMeetingIdRef.current) return;
      setCurrentMeeting(detail);
    } catch (e) {
      console.error("Failed to load meeting detail:", e);
    }
  }, []);

  const selectMeeting = useCallback(async (id: string | null) => {
    // CRITICAL: update ref SYNC before any await. Async reloadCurrentMeeting
    // callers resolving right after the click must see the new id immediately,
    // otherwise they'll fetch the OLD project and clobber the new one.
    currentMeetingIdRef.current = id;
    setCurrentMeetingId(id);
    setCurrentRecordingId(null);
    // Clear stale detail IMMEDIATELY so the sidebar doesn't render the
    // previous meeting's recordings under the newly-clicked meeting's slot
    // during the ~100ms before the fetch resolves. Sidebar will show its
    // "Đang tải…" / empty state until detail lands.
    setCurrentMeeting(null);
    if (!id) {
      return;
    }
    const token = ++meetingFetchTokenRef.current;
    try {
      const detail = await api.meetings.get(id);
      // User clicked another meeting between fire and response — discard.
      if (meetingFetchTokenRef.current !== token) return;
      if (currentMeetingIdRef.current !== id) return;
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

  // MoM pane is now a togglable side panel (Notta-style). Default open
  // so existing flows still see it; users can hide it via the floating
  // rail button to reclaim transcript width.
  const [momOpen, setMomOpenState] = useState<boolean>(
    () => localStorage.getItem("mee.momOpen") !== "false",
  );
  const setMomOpen = useCallback((v: boolean) => {
    localStorage.setItem("mee.momOpen", String(v));
    setMomOpenState(v);
  }, []);
  const toggleMom = useCallback(() => {
    setMomOpenState((v) => {
      localStorage.setItem("mee.momOpen", String(!v));
      return !v;
    });
  }, []);

  // Meeting "Chi tiết" panel — the expandable form (date, attendees,
  // vocab, model picks) lives in MeetingControl but the toggle button
  // now sits in Topbar, so the state needs to be shared.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const toggleDetails = useCallback(() => setDetailsOpen((v) => !v), []);

  // Floating-rail side panes — Insights (analytics) + Comments.
  // Both slide in from the right like ChatPane; only one of the three
  // (chat / insights / comments) is meaningful at a time so toggling
  // one closes the others.
  const [insightsOpen, setInsightsOpenState] = useState(false);
  const [commentsOpen, setCommentsOpenState] = useState(false);
  const toggleInsights = useCallback(() => {
    setInsightsOpenState((v) => {
      const next = !v;
      if (next) setCommentsOpenState(false);
      return next;
    });
  }, []);
  const toggleComments = useCallback(() => {
    setCommentsOpenState((v) => {
      const next = !v;
      if (next) setInsightsOpenState(false);
      return next;
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

  // ─── Audio device preferences ─────────────────────────────────
  // Persisted deviceIds for mic + speaker. null = use browser/OS
  // default. Mic deviceId is also read directly by useLiveRecording
  // (legacy localStorage path); the state here keeps the UI in sync.
  const [audioInputDeviceId, setAudioInputDeviceIdState] = useState<string | null>(
    () => localStorage.getItem("mee.audioInputDeviceId") || null,
  );
  const setAudioInputDeviceId = useCallback((id: string | null) => {
    if (id) localStorage.setItem("mee.audioInputDeviceId", id);
    else localStorage.removeItem("mee.audioInputDeviceId");
    setAudioInputDeviceIdState(id);
  }, []);
  const [audioOutputDeviceId, setAudioOutputDeviceIdState] = useState<string | null>(
    () => localStorage.getItem("mee.audioOutputDeviceId") || null,
  );
  const setAudioOutputDeviceId = useCallback((id: string | null) => {
    if (id) localStorage.setItem("mee.audioOutputDeviceId", id);
    else localStorage.removeItem("mee.audioOutputDeviceId");
    setAudioOutputDeviceIdState(id);
  }, []);
  const [audioDevices, setAudioDevices] = useState<{
    inputs: MediaDeviceInfo[];
    outputs: MediaDeviceInfo[];
  }>({ inputs: [], outputs: [] });
  const refreshAudioDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      // Chrome/Edge emit synthetic "default" and "communications"
      // deviceIds that mirror whichever real device is currently the
      // system default. We render our own "System default" entry, so
      // filter these out to avoid duplicates in the picker.
      const isAlias = (d: MediaDeviceInfo) =>
        d.deviceId === "default" || d.deviceId === "communications";
      setAudioDevices({
        inputs: all.filter((d) => d.kind === "audioinput" && !isAlias(d)),
        outputs: all.filter((d) => d.kind === "audiooutput" && !isAlias(d)),
      });
    } catch {
      /* ignore — surface in UI as empty list */
    }
  }, []);
  // Initial enumerate + react to device hotplug.
  useEffect(() => {
    void refreshAudioDevices();
    if (!navigator.mediaDevices?.addEventListener) return;
    const onChange = () => { void refreshAudioDevices(); };
    navigator.mediaDevices.addEventListener("devicechange", onChange);
    return () => navigator.mediaDevices.removeEventListener("devicechange", onChange);
  }, [refreshAudioDevices]);

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
    (key: StringKey, vars?: Record<string, string | number>) => {
      const raw = (STRINGS[lang] as Record<string, string>)[key] || key;
      if (!vars) return raw;
      // {name} → vars.name. Unmatched placeholders are left as-is so missing
      // keys are visible during development.
      return raw.replace(/\{(\w+)\}/g, (m, k) =>
        vars[k] !== undefined ? String(vars[k]) : m,
      );
    },
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
    momOpen,
    toggleMom,
    setMomOpen,
    detailsOpen,
    toggleDetails,
    insightsOpen,
    toggleInsights,
    commentsOpen,
    toggleComments,
    sidebarOpen,
    toggleSidebar,
    lang,
    setLang,
    t,
    audioInputDeviceId,
    setAudioInputDeviceId,
    audioOutputDeviceId,
    setAudioOutputDeviceId,
    audioDevices,
    refreshAudioDevices,
    momStatus,
    setMomStatus,
    transcriptStatus,
    setTranscriptStatus,
    freshRecordingMoms,
    setRecordingMom,
    freshProjectSummary,
    setProjectSummary,
    confirm,
    generatingRecordings,
    markGeneratingRecording,
    unmarkGeneratingRecording,
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
        accent={confirmState?.accent}
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
