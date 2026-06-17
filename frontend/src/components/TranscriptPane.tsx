// TranscriptPane — upload audio, display transcript, generate MoM / project summary.
//
// Behavior wired in this phase:
//   1. When currentRecordingId changes → fetch transcript segments → join into text
//   2. Upload audio (.wav/.mp3/.m4a) → /api/transcribe → /import-transcript → reload
//   3. Click "Biên bản phiên này" → /api/recordings/{id}/generate-mom → reload
//   4. Click "Tổng kết project" → /api/meetings/{id}/generate-project-summary → reload
//   5. Raw/Clean toggle: Raw = textarea joined; Clean = call /clean if not cached
//   6. Save .txt: download textarea content
//
// Live recording (WebSocket) is deferred to Phase B.2.
import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { api, ApiError } from "../api/client";
import type { CleanResponse, MoMJson, RawSegment } from "../types/api";
import { NottaCleanView } from "./NottaCleanView";
import { FloatingRail } from "./FloatingRail";
import { useLiveRecording, type LiveSegment } from "../hooks/useLiveRecording";
import { ConfirmDialog } from "./ConfirmDialog";

type ViewMode = "raw" | "clean";

interface Props {
  /** When set, replace transcript content with this overview (used for project-overview mode). */
  overviewContent?: React.ReactNode;
}

export function TranscriptPane({ overviewContent }: Props = {}) {
  const {
    currentRecordingId,
    currentMeetingId,
    currentMeeting,
    reloadCurrentMeeting,
    reloadMeetings,
    selectMeeting,
    selectRecording,
    setMomStatus,
    setRecordingMom,
    setProjectSummary,
    transcriptStatus: status,
    setTranscriptStatus: setStatus,
    t,
    lang,
    generatingRecordings,
    markGeneratingRecording,
    unmarkGeneratingRecording,
  } = useApp();
  // Derived: true iff backend is generating MoM for the recording the user is
  // CURRENTLY viewing. Survives recording-switch — if user comes back, we still
  // know gen is in flight (set was kept in context).
  const isGenerating = !!(
    currentRecordingId && generatingRecordings.has(currentRecordingId)
  );
  const [view, setView] = useState<ViewMode>("raw");
  // Background clean status: poll backend every 3s while running.
  // Lets the user see a progress bar instead of guessing why Clean tab
  // is slow when they click it right after upload.
  const [cleanStatus, setCleanStatusState] =
    useState<"idle" | "running" | "done">("idle");
  const [cleanProgress, setCleanProgress] = useState<{
    phase: "cleaning" | "saving";
    current_chunk: number;
    total_chunks: number;
    started_at_ms: number;
    raw_chars: number;
  } | null>(null);
  // Ping clean-status NOW (used right after upload / stop-record so the UI
  // flips from idle→running without waiting for the next 3s poll tick).
  const pingCleanStatus = useCallback(async () => {
    if (!currentRecordingId) return;
    try {
      const r = await api.recordings.cleanStatus(currentRecordingId);
      setCleanStatusState(r.status);
      setCleanProgress(r.progress);
    } catch { /* ignore */ }
  }, [currentRecordingId]);
  const [rawText, setRawText] = useState<string>("");
  // Structured segments with start_ms/end_ms — drives Notta-style audio sync.
  // Loaded from /api/recordings/{id}/transcript. Empty for live-record before
  // post-record diarize finishes.
  const [rawSegments, setRawSegments] = useState<RawSegment[]>([]);
  // null = not loaded yet; [] = loaded but no segments; non-empty = ready.
  // Concrete type rather than `CleanResponse["clean_segments"]` because the
  // response field is now optional (task-id shape carries no segments).
  type CleanSeg = NonNullable<CleanResponse["clean_segments"]>[number];
  const [cleanSegs, setCleanSegs] = useState<CleanSeg[] | null>(null);
  // editedHtml/preMapped/availableClusters/editorRev are kept in state purely
  // so the /clean response can populate them — NottaCleanView fetches its
  // own data from the API endpoints instead. The setters are still used by
  // the reload/regenerate paths; discarding the values silences TS6133.
  const [, setEditedHtml] = useState<string | null>(null);
  const [clusterMapping, setClusterMapping] = useState<Record<string, string>>({});
  const [, setPreMappedClusters] = useState<string[]>([]);
  const [, setAvailableClusters] = useState<string[]>([]);
  const [, setEditorRev] = useState(0);

  /** /clean now returns either:
   *  - {cached:true, clean_segments:[...]}             — cache hit, inline result
   *  - {task_id, status:"queued", mode:"celery"}       — dispatched, poll task
   *  - {cached:false, clean_segments:[...]}            — inline fallback (broker down)
   *
   * This wrapper polls when needed and ALWAYS returns the cached result via
   * a second /clean call once the task finishes. Lets callers stay simple. */
  async function requestClean(rid: string, regenerate: boolean) {
    const first = await api.recordings.clean(rid, regenerate);
    if (!first.task_id) return first;
    // Dispatched to Celery — poll until SUCCESS, then re-fetch /clean which
    // now hits the DB cache and returns full segments + cluster_mapping.
    const targetRid = rid;
    while (true) {
      await new Promise((r) => setTimeout(r, 5_000));
      if (currentRecordingIdRef.current !== targetRid) {
        // User switched recording mid-poll. Abort gracefully — when they
        // return, reloadClean() will hit the cache (task likely finished).
        throw new Error("recording switched");
      }
      let st;
      try {
        st = await api.tasks.status(first.task_id);
      } catch {
        continue; // transient network blip — keep polling
      }
      if (st.state === "SUCCESS") {
        return await api.recordings.clean(rid, false);
      }
      if (st.state === "FAILURE") {
        const msg = st.error || "Cleaner task failed";
        throw new ApiError(500, msg);
      }
      // PENDING / STARTED / RETRY → continue polling
    }
  }

  async function reloadClean() {
    if (!currentRecordingId) return;
    try {
      const r = await requestClean(currentRecordingId, false);
      setCleanSegs(r.clean_segments || []);
      setEditedHtml(r.edited_html || null);
      setClusterMapping(r.cluster_mapping || {});
      setPreMappedClusters(r.pre_mapped_clusters || []);
      setAvailableClusters(r.available_clusters || []);
      setEditorRev((v) => v + 1);
    } catch {
      /* ignore — banner already set elsewhere */
    }
  }

  // Re-pull the raw transcript segments after a structural change (e.g. a
  // user split one block into two via Enter in the Notta edit view). Only
  // refreshes rawSegments/rawText — leaves clusterMapping + clean cache
  // intact (the split keeps cluster/speaker, and clean_segments resyncs on
  // the next regenerate, matching existing edit-then-regenerate behaviour).
  async function reloadTranscript() {
    if (!currentRecordingId) return;
    try {
      const r = await api.recordings.transcript(currentRecordingId);
      setRawText(r.transcript || "");
      setRawSegments(r.segments || []);
      dbTextRef.current = r.transcript || "";
    } catch {
      /* ignore — transient fetch error, user can retry */
    }
  }
  const [busy, setBusy] = useState(false);
  // True only while the SSE upload pipeline (faster-whisper + diarize +
  // word-align) is mid-flight. NottaCleanView hides the segment list and
  // shows a loading placeholder while this is on; once it flips to false
  // we have the full transcript + word timestamps and karaoke auto-plays
  // from t=0. Kept separate from `busy` because `busy` is also true for
  // unrelated work (cleaner regen, MoM gen) where we should keep showing
  // the existing transcript.
  const [streamingActive, setStreamingActive] = useState(false);
  // One-shot signal to NottaCleanView: a fresh upload just finished →
  // please auto-play audio + karaoke from t=0. Flips on right when
  // streamingActive flips off; consumed once by NottaCleanView's ref
  // guard. Stays sticky so accidental re-renders don't lose the cue.
  const [freshUpload, setFreshUpload] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Ref tracking the CURRENT recording id — used by async generators to
  // detect if user switched recordings mid-flight. Pure state lookups inside
  // a long-running async closure read the captured (stale) value from when
  // the callback was created, so we shadow it via ref that's kept in sync.
  const currentRecordingIdRef = useRef(currentRecordingId);
  useEffect(() => {
    currentRecordingIdRef.current = currentRecordingId;
    // Switching recording mid-generation. Reset the LOCAL busy + status so
    // they don't bleed across recordings. If the recording we're switching
    // TO is itself still generating in the background, re-apply the
    // "Đang tạo…" banner so the user knows.
    setBusy(false);
    setStatus(null);
    if (currentRecordingId && generatingRecordings.has(currentRecordingId)) {
      setMomStatus({
        kind: "assessing",
        msg: t("momPane.generatingTitle"),
      });
    } else {
      setMomStatus(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRecordingId]);

  // ─── Live record (WebSocket Whisper) ───
  // Each Record session APPENDS to whatever the textarea already holds —
  // doesn't wipe. completedRef = segments captured in THIS session only.
  // dbTextRef = snapshot of rawText as last seen from DB (after fetch). Used
  // by switchView to detect "user typed/pasted into textarea" vs "text is
  // just what came from DB" — only the former needs to be imported before
  // /clean runs. Without this check, every Clean-tab switch would re-import
  // identical text → backend wipes clean_segments → /clean re-runs LLM.
  const dbTextRef = useRef<string>("");
  // baseTextRef = textarea content frozen at the moment Record was clicked.
  const completedRef = useRef<LiveSegment[]>([]);
  const baseTextRef = useRef<string>("");

  // [mm:ss] from segment start time (string or number, seconds).
  // Returns "" when start is missing/invalid so the line still renders cleanly.
  function fmtLiveTime(start: string | number | undefined): string {
    if (start === undefined || start === null) return "";
    const s = typeof start === "string" ? parseFloat(start) : start;
    if (!isFinite(s) || s < 0) return "";
    const m = Math.floor(s / 60);
    const ss = Math.floor(s % 60);
    return `[${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}] `;
  }

  const onLiveSegments = useCallback((segs: LiveSegment[]) => {
    for (const s of segs) {
      if (s.completed && !completedRef.current.some((c) => c.start === s.start && c.text === s.text)) {
        completedRef.current.push(s);
      }
    }
    const inProgress = segs.filter((s) => !s.completed);
    // Live text shows [mm:ss] prefix per line so user sees absolute time
    // of each utterance during recording. Speaker tags come AFTER stop
    // (post_record_diarize); not available real-time with batch pyannote.
    const sessionLines = completedRef.current
      .map((s) => {
        const txt = s.text.trim();
        if (!txt) return "";
        return `${fmtLiveTime(s.start)}${txt}`;
      })
      .filter(Boolean);
    if (inProgress.length > 0) {
      const last = inProgress[inProgress.length - 1];
      sessionLines.push(`${fmtLiveTime(last.start)}${last.text.trim()} …`);
    }
    const sessionText = sessionLines.join("\n");
    // Merge: base + (blank line separator if base had content) + this session
    const base = baseTextRef.current;
    const next = base ? (sessionText ? `${base}\n${sessionText}` : base) : sessionText;
    setRawText(next);
  }, []);
  const onLiveStatus = useCallback(
    (kind: "info" | "connecting" | "recording" | "error" | "idle", msg: string) => {
      const map: Record<typeof kind, "info" | "assessing" | "success" | "error"> = {
        info: "info",
        connecting: "assessing",
        recording: "success",
        error: "error",
        idle: "info",
      };
      // While recording, append a hint that speaker tags are post-record.
      // Pyannote 3.1 is batch-only — can't stream cluster IDs consistently
      // (see post_record_diarize for the post-stop run).
      const finalMsg =
        kind === "recording"
          ? t("live.recordingSuffix", { msg })
          : msg;
      setStatus({ kind: map[kind], msg: finalMsg });
    },
    [setStatus],
  );
  // Keep WS live preview ON regardless of STT model — the user wants to
  // see text appear as they speak. WS goes through MaaS Whisper (fast,
  // segment-level only). After stop(), if the recording's STT model is
  // faster-whisper, handleStopRecord re-pass the captured audio for
  // word-level timestamps (see below). End result: live preview during
  // recording + word-accurate final transcript after stop.
  const live = useLiveRecording({
    uid: currentRecordingId || "",
    language: "vi",
    onSegments: onLiveSegments,
    onStatus: onLiveStatus,
  });

  // Modal that prompts the user to create / open a project when they
  // try to record / upload before selecting one. Opening the modal sets
  // the pending action — on confirm we auto-create a default project +
  // recording and re-fire that action.
  const [noProjectModal, setNoProjectModal] = useState<
    null | { action: "record" | "upload"; uploadFile?: File }
  >(null);
  const [creatingProject, setCreatingProject] = useState(false);

  async function ensureProjectAndRecording(): Promise<{
    meetingId: string;
    recordingId: string;
  } | null> {
    // If we already have both, no-op.
    if (currentMeetingId && currentRecordingId) {
      return { meetingId: currentMeetingId, recordingId: currentRecordingId };
    }
    setCreatingProject(true);
    try {
      let mid = currentMeetingId;
      if (!mid) {
        const m = await api.meetings.create({
          title: t("meeting.titlePlaceholder") || "Phiên họp chưa đặt tên",
        });
        mid = m.id;
        await reloadMeetings();
        await selectMeeting(mid);
      }
      // Create the first recording — backend auto-inherits attendees /
      // vocab from the previous sibling, so a brand-new project starts
      // empty which is fine.
      const isEn = t("sidebar.recordingPlaceholder") === "Untitled recording";
      const r = await api.recordings.create(mid, `${isEn ? "Meeting" : "Phiên"} 1`);
      await reloadCurrentMeeting();
      selectRecording(r.id);
      return { meetingId: mid, recordingId: r.id };
    } catch (e) {
      alert(t("transcriptPane.error.create", { msg: (e as Error).message }));
      return null;
    } finally {
      setCreatingProject(false);
    }
  }

  async function handleStartRecord() {
    if (!currentRecordingId) {
      // Show the auto-create modal instead of a bare alert. User can
      // confirm to auto-create a project + recording, or cancel.
      setNoProjectModal({ action: "record" });
      return;
    }
    // Snapshot current text BEFORE starting — new live segments will be
    // appended below it (record/stop/record again keeps prior content).
    baseTextRef.current = rawText;
    completedRef.current = [];
    await live.start();
  }

  async function confirmNoProjectAndProceed() {
    const pending = noProjectModal;
    setNoProjectModal(null);
    if (!pending) return;
    const ok = await ensureProjectAndRecording();
    if (!ok) return;
    if (pending.action === "record") {
      // Slight delay so React commits the recording-id state before we
      // start the WS — useLiveRecording reads currentRecordingId via
      // closure and we want the fresh value.
      window.setTimeout(() => { void handleStartRecord(); }, 50);
    }
    // (upload path can be wired later — for now the upload button
    // handler shows its own alert; this modal only covers record.)
  }

  async function handleStopRecord() {
    // Capture the recorded audio blob BEFORE the WS path drops its state.
    const audioBlob = await live.stop();

    if (!currentMeetingId || !currentRecordingId) return;

    // Decide: if STT model = faster-whisper AND we have audio bytes,
    // re-transcribe the captured audio via the SSE faster-whisper
    // pipeline to recover word-level timestamps the WS path doesn't
    // provide. Otherwise fall back to the original importTranscript
    // (text-only) path so existing models (MaaS / large-v3) still work.
    const currentRec = currentMeeting?.recordings.find((r) => r.id === currentRecordingId);
    const sttModel = currentRec?.stt_model || currentMeeting?.stt_model || "";
    const useFasterWhisperRepass = sttModel === "faster_whisper" && audioBlob.size > 1024;

    if (useFasterWhisperRepass) {
      // Wrap the blob as a File so api.transcribeStream accepts it.
      const ext = audioBlob.type.includes("webm") ? "webm" : "wav";
      const file = new File([audioBlob], `live-${Date.now()}.${ext}`, {
        type: audioBlob.type || "audio/webm",
      });
      // Reuse the same vocab + attendees logic as the upload path so
      // recording-specific hints reach faster-whisper.
      const attendeesStr =
        currentRec?.attendees?.map((a) => a.name).filter(Boolean).join(", ") || "";
      const vocabStr = [
        (currentMeeting?.vocab_hints || "").trim(),
        (currentRec?.vocab_hints || "").trim(),
      ].filter(Boolean).join(", ");

      setView("clean");
      setStreamingActive(true);
      setFreshUpload(false);
      setRawSegments([]);
      setRawText("");
      setStatus({
        kind: "assessing",
        msg: t("transcriptPane.status.reAnalyze"),
      });

      try {
        const liveSegs: RawSegment[] = [];
        const collected: Array<{
          text: string; speaker: string; start: number; end: number;
          words: { text: string; start: number; end: number }[];
        }> = [];
        for await (const ev of api.transcribeStream(
          file, "vi", vocabStr, attendeesStr, currentRecordingId, "faster_whisper",
        )) {
          if (ev.type === "segment") {
            collected.push({
              text: ev.text, speaker: ev.speaker,
              start: ev.start, end: ev.end, words: ev.words,
            });
            liveSegs.push({
              seq: liveSegs.length + 1,
              text: ev.text, speaker: ev.speaker,
              start_ms: Math.round(ev.start * 1000),
              end_ms: Math.round(ev.end * 1000),
              words: ev.words,
            });
            setRawSegments([...liveSegs]);
            if (liveSegs.length === 1) setFreshUpload(true);
            setStatus({
              kind: "assessing",
              msg: t("transcriptPane.status.live", { n: liveSegs.length }),
            });
            await new Promise<void>((r) => window.requestAnimationFrame(() => r()));
          } else if (ev.type === "error") {
            throw new ApiError(500, ev.detail);
          }
        }
        const fullText = liveSegs
          .map((s) => `[${s.speaker || "?"}] ${s.text}`)
          .join("\n");
        setRawText(fullText);
        // Import the new transcript so Clean + Generate MoM see it.
        // CRITICAL: pass the structured `segments` array (with words +
        // start_ms / end_ms timestamps) — without this the backend
        // splits text by lines and stores segments with NULL timestamps,
        // killing karaoke word-level highlight on playback.
        await api.meetings.importTranscript(currentMeetingId, {
          text: fullText,
          segments: collected.map((c) => ({
            text: c.text,
            speaker: c.speaker,
            start: c.start,
            end: c.end,
            words: c.words,
          })),
          recording_id: currentRecordingId,
          replace: true,
          duration_sec: liveSegs.length > 0
            ? Math.ceil((liveSegs[liveSegs.length - 1].end_ms || 0) / 1000)
            : null,
        });
        dbTextRef.current = fullText;
        await reloadCurrentMeeting();
        pingCleanStatus();
        setStatus({ kind: "success", msg: t("live.done") });
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setStatus({ kind: "error", msg: t("transcriptPane.error.transcribe", { msg }) });
      } finally {
        setStreamingActive(false);
      }
      return;
    }

    // Legacy path: VNG MaaS / Whisper-large-v3 — persist the WS-accumulated
    // text directly. No word-level timestamps available for these models.
    if (rawText.trim()) {
      setStatus({
        kind: "assessing",
        msg: t("live.analyzing"),
      });
      try {
        await api.meetings.importTranscript(currentMeetingId, {
          text: rawText,
          recording_id: currentRecordingId,
          replace: false,
        });
        dbTextRef.current = rawText;
        await reloadCurrentMeeting();
        pingCleanStatus();
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setStatus({ kind: "error", msg: t("live.saveError", { msg }) });
      }
    }
  }

  // ─── Poll background clean status ONLY while running ───
  // Initial state seeded from a single tick on recording switch (via the
  // existing pingCleanStatus call elsewhere). Active polling kicks in only
  // when status is "running" — stops as soon as backend reports done/idle.
  // Avoids wasting 1 req/1.5s when nothing's happening.
  useEffect(() => {
    if (!currentRecordingId || cleanStatus !== "running") return;
    let cancelled = false;
    const rid = currentRecordingId;
    async function tick() {
      if (cancelled) return;
      try {
        const r = await api.recordings.cleanStatus(rid);
        if (cancelled) return;
        setCleanStatusState(r.status);
        setCleanProgress(r.progress);
      } catch {
        /* swallow — next tick retries */
      }
    }
    const interval = window.setInterval(tick, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [currentRecordingId, cleanStatus]);

  // Watch cleanStatus running → done transition. When clean finishes after a
  // live-record stop, the user sees their banner flip from "Đang phân tích…"
  // to "✓ Hoàn tất — speaker tags + clean transcript đã sẵn sàng". Refetch
  // the meeting so the editor picks up speaker-tagged segments.
  const prevCleanStatusRef = useRef<typeof cleanStatus>("idle");
  useEffect(() => {
    const prev = prevCleanStatusRef.current;
    if (prev === "running" && cleanStatus === "done") {
      setStatus({
        kind: "success",
        msg: t("live.done"),
      });
      reloadCurrentMeeting();
    }
    prevCleanStatusRef.current = cleanStatus;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cleanStatus]);

  // Seed status on recording switch — kicks off polling above if status
  // turns out to be "running".
  useEffect(() => {
    if (!currentRecordingId) {
      setCleanStatusState("idle");
      setCleanProgress(null);
      return;
    }
    pingCleanStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRecordingId]);

  // ─── Load transcript whenever the selected recording changes ───
  useEffect(() => {
    // Clear the karaoke arm whenever the recording changes — only a
    // fresh upload (handleUpload) should re-set freshUpload=true.
    setFreshUpload(false);
    if (!currentRecordingId) {
      setRawText("");
      setRawSegments([]);
      setCleanSegs(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await api.recordings.transcript(currentRecordingId);
        if (cancelled) return;
        setRawText(r.transcript || "");
        setRawSegments(r.segments || []);
        dbTextRef.current = r.transcript || "";
        setCleanSegs(null);    // invalidate clean cache on recording switch
        setEditedHtml(null);
        setClusterMapping({});
        setPreMappedClusters([]);
        setAvailableClusters([]);
        // If user was on the Clean tab when they switched recording, fetch
        // the new recording's clean automatically — otherwise the pane goes
        // blank and they'd have to bounce Raw→Clean to trigger a load.
        if (view === "clean") {
          try {
            const c = await api.recordings.clean(currentRecordingId, false);
            if (cancelled) return;
            // task_id response means dispatched to Celery — skip silently;
            // user can hit Clean tab again or the polling fires on next try.
            if (c.task_id) return;
            setCleanSegs(c.clean_segments || []);
            setClusterMapping(c.cluster_mapping || {});
            setPreMappedClusters(c.pre_mapped_clusters || []);
            setAvailableClusters(c.available_clusters || []);
            setEditedHtml(c.edited_html || null);
          } catch {
            // Clean not generated yet for this recording — fall back to Raw
            // so user sees something instead of an empty Clean pane.
            if (!cancelled) setView("raw");
          }
        }
      } catch (e) {
        if (!cancelled) {
          setStatus({ kind: "error", msg: t("transcriptPane.error.loadTranscript", { msg: (e as Error).message }) });
        }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRecordingId]);

  // ─── Upload audio file ───
  async function handleUpload(file: File) {
    if (!currentRecordingId) {
      alert(t("transcriptPane.alert.noMeeting"));
      return;
    }
    if (!currentMeetingId) return;
    setBusy(true);
    setStatus({ kind: "assessing", msg: t("transcriptPane.status.assess", { name: file.name }) });
    try {
      // Pass attendees to bias Whisper STT + diarization (correct name
      // spellings, expected speaker count). Defined in MeetingControl
      // details panel.
      // Per-meeting-event metadata moved to recording in migration 0012.
      // Look up the current recording for attendees + recording-level vocab.
      const currentRec = currentMeeting?.recordings.find(
        (r) => r.id === currentRecordingId,
      );
      const attendeesStr =
        currentRec?.attendees?.map((a) => a.name).filter(Boolean).join(", ") || "";
      // Vocab is 2-tier: project default + recording-specific. Concat both.
      const vocabParts = [
        (currentMeeting?.vocab_hints || "").trim(),
        (currentRec?.vocab_hints || "").trim(),
      ].filter(Boolean);
      const vocabStr = vocabParts.join(", ");
      // STT routing: prefer recording.stt_model, fall back to meeting default,
      // fall back to "" → backend resolves DEFAULT_STT. Without this the
      // upload always hit VNG MaaS whisper regardless of dropdown choice.
      const sttModel =
        currentRec?.stt_model || currentMeeting?.stt_model || "";

      // Notta-style streaming path — only faster-whisper supports it. We
      // build up `transcribeResp` incrementally from SSE events instead of
      // waiting for one batch response, and push each segment into FE
      // state as it lands so the user sees words appearing live.
      let transcribeResp: Awaited<ReturnType<typeof api.transcribe>>;
      if (sttModel === "faster_whisper") {
        // Switch to Clean view IMMEDIATELY so the loading placeholder
        // (Notta still-processing UI) shows there instead of the raw
        // textarea, which would otherwise flash a partial dump as
        // segments stream in. Karaoke triggers when this flag flips off.
        setView("clean");
        setStreamingActive(true);
        // Reset karaoke arm BEFORE the stream starts so NottaCleanView
        // sees a clean false→true transition when the first segment
        // arrives. Without this, a second upload to the same recording
        // would never re-fire karaoke (freshUpload was already true
        // from the previous upload).
        setFreshUpload(false);
        // Clear any prior recording's segments so the loading placeholder
        // doesn't briefly show stale data before the first event lands.
        setRawSegments([]);
        setRawText("");
        // setStatus({
        //   kind: "assessing",
        //   msg: `Đang xử lý "${file.name}" — STT + diarize + word-align…`,
        // });
        const liveSegs: RawSegment[] = [];
        const collected: Array<{
          text: string;
          speaker: string;
          start: number;
          end: number;
          words: { text: string; start: number; end: number }[];
        }> = [];
        let clusterEmb: Record<string, number[]> = {};
        for await (const ev of api.transcribeStream(
          file, "vi", vocabStr, attendeesStr, currentRecordingId || "", "faster_whisper",
        )) {
          if (ev.type === "diarize") {
            clusterEmb = ev.embeddings || {};
          } else if (ev.type === "segment") {
            collected.push({
              text: ev.text,
              speaker: ev.speaker,
              start: ev.start,
              end: ev.end,
              words: ev.words,
            });
            liveSegs.push({
              seq: liveSegs.length + 1,
              text: ev.text,
              speaker: ev.speaker,
              start_ms: Math.round(ev.start * 1000),
              end_ms: Math.round(ev.end * 1000),
              words: ev.words,
            });
            // Append LIVE so blocks appear as backend produces them. The
            // visibility filter inside NottaCleanView gates whether each
            // block renders based on audio progress (maxRevealedSec).
            // For long audio (1-2h), this lets the user see the early
            // blocks + start listening while the rest is still decoding.
            setRawSegments([...liveSegs]);
            // Arm karaoke as soon as the FIRST segment arrives — don't
            // wait for the whole pipeline. NottaCleanView's ref guard
            // ensures it triggers exactly once per recording.
            if (liveSegs.length === 1) {
              setFreshUpload(true);
            }
            setStatus({
              kind: "assessing",
              msg: t("transcriptPane.status.upload", { name: file.name, n: liveSegs.length }),
            });
            // Yield to browser so React commits the new segment before
            // we process the next SSE frame. Without this, React 18
            // batches and we re-render only at the end of the burst.
            await new Promise<void>((resolve) =>
              window.requestAnimationFrame(() => resolve()),
            );
          } else if (ev.type === "error") {
            throw new ApiError(500, ev.detail);
          }
          // 'meta' and 'done' are housekeeping; ignored.
        }
        // Pipeline finished — flush the buffered segments into state in one
        // shot. NottaCleanView then renders everything together AND the
        // karaoke effect (triggered by streamingActive flipping off below)
        // auto-plays the audio + reveals words synced to playback. No more
        // flicker because nothing was visible during processing.
        setRawSegments(liveSegs);
        setRawText(
          liveSegs
            .map((s) => {
              const sec = Math.floor((s.start_ms ?? 0) / 1000);
              const mm = String(Math.floor(sec / 60)).padStart(2, "0");
              const ss = String(sec % 60).padStart(2, "0");
              return `[${mm}:${ss}] ${s.speaker || ""}: ${s.text}`;
            })
            .join("\n"),
        );
        setStreamingActive(false);
        // Arm karaoke. NottaCleanView consumes this via a ref guard so
        // it only triggers once per recording — subsequent renders with
        // the flag still true are no-ops.
        setFreshUpload(true);
        transcribeResp = {
          text: collected.map((s) => s.text).join("\n"),
          segments: collected,
          cluster_embeddings: clusterEmb,
          // sample_audio_b64 / pending_diarize_path don't apply to the
          // streaming path — leave undefined so the downstream import
          // doesn't try to forward bogus data.
          sample_audio_b64: undefined,
          pending_diarize_path: undefined,
        };
      } else {
        transcribeResp = await api.transcribe(
          file, "vi", vocabStr, attendeesStr, currentRecordingId || "", sttModel,
        );
      }
      const text = transcribeResp.text;
      if (!text?.trim()) {
        setStatus({ kind: "error", msg: t("transcriptPane.error.noVoice") });
        return;
      }
      // Format text with [mm:ss] SPEAKER_NN: prefixes for the Raw textarea
      // view, when PhoWhisper returned structured segments.
      const phoSegs = transcribeResp.segments;
      const formattedText = phoSegs?.length
        ? phoSegs
            .map((s) => {
              const spk = (s.speaker || "").trim();
              const body = (s.text || "").trim();
              let ts = "";
              if (typeof s.start === "number") {
                const sec = Math.floor(s.start);
                const mm = String(Math.floor(sec / 60)).padStart(2, "0");
                const ss = String(sec % 60).padStart(2, "0");
                ts = `[${mm}:${ss}] `;
              }
              return body ? `${ts}${spk ? spk + ": " : ""}${body}` : "";
            })
            .filter(Boolean)
            .join("\n")
        : text;
      setRawText(formattedText);
      dbTextRef.current = formattedText;
      setStatus({ kind: "assessing", msg: t("transcriptPane.saving") });
      const imp = await api.meetings.importTranscript(currentMeetingId, {
        text,  // legacy fallback (only used if segments absent)
        // Pass structured segments — backend uses these to populate
        // transcript_segments with speaker + timestamps.
        segments: phoSegs?.map((s) => ({
          text: s.text,
          speaker: s.speaker || null,
          start: s.start ?? null,
          end: s.end ?? null,
          // Forward word-level timestamps when STT returned them
          // (faster-whisper). Backend persists into transcript_segments.words.
          words: s.words ?? null,
        })),
        recording_id: currentRecordingId,
        replace: false,
        // Forward PhoWhisper's per-cluster embeddings (if STT returned them)
        // so the Clean step's matcher can recognise returning speakers.
        cluster_embeddings:
          (transcribeResp as { cluster_embeddings?: Record<string, number[]> })
            .cluster_embeddings || null,
        // Local pyannote also yields 3s sample WAVs — forward so user can
        // play them in SpeakerMapper before saving the voiceprint name.
        sample_audio_b64:
          (transcribeResp as { sample_audio_b64?: Record<string, string> })
            .sample_audio_b64 || null,
        // Chunked upload — backend staged the full WAV; pass the path
        // so import-transcript can dispatch the async pyannote task.
        pending_diarize_path:
          (transcribeResp as { pending_diarize_path?: string | null })
            .pending_diarize_path || null,
      });
      await reloadCurrentMeeting();
      // Background clean was just triggered by import-transcript endpoint —
      // flip the badge to "running" right away.
      pingCleanStatus();
      const baseMsg = `Đã transcribe "${file.name}" ✓ (${imp.segments_count} đoạn)`;
      const diarizeTaskId =
        (imp as { diarize_task_id?: string | null }).diarize_task_id || null;
      if (diarizeTaskId) {
        // Async pyannote running in background. Poll until SUCCESS / FAILURE,
        // then reload meeting so speaker_samples + cluster_mapping appear.
        setStatus({
          kind: "success",
          msg: `${baseMsg} — đang phân tích speakers ở background (~30 phút)`,
        });
        (async () => {
          const targetRid = currentRecordingIdRef.current;
          while (true) {
            await new Promise((r) => setTimeout(r, 30_000));
            try {
              const st = await api.tasks.status(diarizeTaskId);
              if (st.state === "SUCCESS") {
                if (currentRecordingIdRef.current === targetRid) {
                  setStatus({
                    kind: "success",
                    msg: t("live.done"),
                  });
                  await reloadCurrentMeeting();
                }
                return;
              }
              if (st.state === "FAILURE") {
                if (currentRecordingIdRef.current === targetRid) {
                  setStatus({
                    kind: "error",
                    msg: `Phân tích speakers thất bại: ${st.error || "unknown"}`,
                  });
                }
                return;
              }
              // PENDING / STARTED / RETRY → keep waiting
            } catch (e) {
              // Network blip — keep polling
              console.warn("[diarize-poll] status check failed", e);
            }
          }
        })();
      } else {
        setStatus({ kind: "success", msg: baseMsg });
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setStatus({ kind: "error", msg: t("sidebar.error.generic", { msg }) });
    } finally {
      setBusy(false);
      // Defensive: clear streaming flag even on error/abort so the loading
      // placeholder doesn't strand the UI in "processing forever".
      setStreamingActive(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  // ─── Generate per-recording MoM ───
  // Status goes to MoM pane (where the result will appear), not Transcript pane.
  async function handleGenerateMom() {
    if (!currentRecordingId || !currentMeetingId) return;
    // Block re-entry: if backend is already generating for this recording,
    // don't fire another call. UI button is also disabled on isGenerating.
    if (generatingRecordings.has(currentRecordingId)) return;
    // Snapshot the recording + meeting we started on. Gen state is tracked
    // in context (survives recording-switch); local status banner is only
    // shown while user is still viewing this recording.
    const targetRid = currentRecordingId;
    const targetMid = currentMeetingId;
    const stillOnThis = () => currentRecordingIdRef.current === targetRid;
    markGeneratingRecording(targetRid);
    setMomStatus({ kind: "assessing", msg: t("momPane.generatingTitle") });
    try {
      const currentText = rawText.trim();
      if (currentText) {
        await api.meetings.importTranscript(targetMid, {
          text: currentText,
          recording_id: targetRid,
          replace: false,
        });
      }
      const res = await api.recordings.generateMom(targetRid, lang);
      // Two shapes: Celery enqueued (task_id) vs inline (notes returned).
      let notes: MoMJson | undefined;
      if ("task_id" in res) {
        // Celery path — poll /api/tasks/{id} every 3s until SUCCESS/FAILURE.
        // Hard stop at 15 min to match backend task_time_limit. Caller stays
        // marked as generating across the entire poll loop so UI button
        // remains disabled + status banner sticks.
        const taskId = res.task_id;
        const startedAt = Date.now();
        const POLL_MS = 3000;
        const TIMEOUT_MS = 15 * 60 * 1000;
        while (Date.now() - startedAt < TIMEOUT_MS) {
          await new Promise((r) => setTimeout(r, POLL_MS));
          const status = await api.tasks.status(taskId);
          if (status.state === "SUCCESS") {
            notes = status.result?.notes;
            break;
          }
          if (status.state === "FAILURE" || status.state === "REVOKED") {
            throw new Error(status.error || "MoM generation failed");
          }
          // PENDING / STARTED / RETRY → keep polling
        }
        if (!notes) throw new Error("MoM generation timed out");
      } else {
        // Inline (broker down) path — notes returned directly.
        notes = res.notes;
      }
      // Always cache result under the snapshot id (correct destination).
      if (notes) setRecordingMom(targetRid, notes);
      // reloadCurrentMeeting reads currentMeetingId from a ref (latest), so
      // calling it here is safe — if user has switched to another project,
      // the reload will hit the NEW project's endpoint instead of clobbering
      // it with X's data. Safe to fire-and-forget unconditionally.
      reloadCurrentMeeting();
      // Only mutate visible status when user is still viewing this recording.
      if (stillOnThis()) {
        setMomStatus({ kind: "success", msg: t("live.done") });
        setTimeout(() => setMomStatus(null), 4000);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      if (stillOnThis()) {
        setMomStatus({ kind: "error", msg: t("momPane.error.save", { msg }) });
      }
    } finally {
      unmarkGeneratingRecording(targetRid);
    }
  }

  // ─── Generate project-level summary ───
  async function handleProjectSummary() {
    if (!currentMeetingId) return;
    setBusy(true);
    setMomStatus({ kind: "assessing", msg: t("momPane.summarizing") });
    try {
      const res = await api.meetings.generateProjectSummary(currentMeetingId);
      setProjectSummary(currentMeetingId, res.summary);
      reloadCurrentMeeting();
      setMomStatus({
        kind: "success",
        msg: t("momPane.summarized", { n: res.summary.session_count }),
      });
      setTimeout(() => setMomStatus(null), 4000);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setMomStatus({ kind: "error", msg: t("momPane.error.summarize", { msg }) });
    } finally {
      setBusy(false);
    }
  }

  // Listen for the re-generate-MoM request from MoMPane's "↻ Tạo lại"
  // button. The button lives in MoMPane but the action's heavy lifting
  // (Celery polling, status banners, in-flight guards) lives in
  // handleGenerateMom here — custom DOM event is the lightweight
  // wiring between them.
  useEffect(() => {
    const handler = () => { void handleGenerateMom(); };
    window.addEventListener("mee.regenerate-mom", handler);
    return () => window.removeEventListener("mee.regenerate-mom", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRecordingId, currentMeetingId, rawText, lang]);

  // ─── Switch view: fetch clean segments lazily ───
  const switchView = useCallback(
    async (next: ViewMode) => {
      setView(next);
      if (next !== "clean" || !currentRecordingId || cleanSegs !== null) return;
      setBusy(true);
      setStatus({ kind: "assessing", msg: t("transcriptPane.status.clean") });
      try {
        // If user typed/pasted text directly into the Raw textarea but never
        // recorded/uploaded (no auto-save path triggered), DB is empty for
        // this recording → /clean would fail with "No segments to clean".
        // Only persist textarea content if the user actually changed it
        // (pasted / typed). Re-importing identical text wipes the cached
        // clean_segments on the backend → /clean would re-run LLM each time
        // user toggles to Clean tab.
        const userEditedText =
          rawText.trim() && rawText !== dbTextRef.current;
        if (currentMeetingId && userEditedText) {
          try {
            await api.meetings.importTranscript(currentMeetingId, {
              text: rawText,
              recording_id: currentRecordingId,
              replace: false,
            });
            dbTextRef.current = rawText;
          } catch {
            // Non-fatal — /clean will surface the real problem below if any.
          }
        }
        // Initial Clean — when LLM call is dispatched to Celery, this
        // resolves only after the worker finishes (the wrapper polls).
        setStatus({ kind: "assessing", msg: t("transcriptPane.status.clean") });
        const r = await requestClean(currentRecordingId, false);
        setCleanSegs(r.clean_segments || []);
        setEditedHtml(r.edited_html || null);
        setClusterMapping(r.cluster_mapping || {});
        setPreMappedClusters(r.pre_mapped_clusters || []);
        setAvailableClusters(r.available_clusters || []);
        setStatus({
          kind: "success",
          msg: r.cached ? "Clean (cache) ✓" : "Clean ✓",
        });
        // After /clean returns, background task is finished — refresh status
        // so the progress bar hides immediately (don't wait next poll tick).
        pingCleanStatus();
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setStatus({ kind: "error", msg: t("momPane.error.save", { msg }) });
        setView("raw");
      } finally {
        setBusy(false);
      }
    },
    [currentRecordingId, currentMeetingId, cleanSegs, rawText],
  );

  function regenerateClean() {
    if (!currentRecordingId) return;
    setBusy(true);
    setStatus({ kind: "assessing", msg: t("transcriptPane.status.regen") });
    requestClean(currentRecordingId, true)
      .then((r) => {
        setCleanSegs(r.clean_segments || []);
        setEditedHtml(r.edited_html || null);
        setClusterMapping(r.cluster_mapping || {});
        setPreMappedClusters(r.pre_mapped_clusters || []);
        setAvailableClusters(r.available_clusters || []);
        setStatus({ kind: "success", msg: t("live.done") });
      })
      .catch((e) => {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setStatus({ kind: "error", msg: t("sidebar.error.generic", { msg }) });
      })
      .finally(() => setBusy(false));
  }

  // ─── Save .txt ───
  function handleSaveTxt() {
    if (!rawText.trim()) return;
    const rec = currentMeeting?.recordings.find((r) => r.id === currentRecordingId);
    const label = rec?.session_label || "transcript";
    const blob = new Blob([rawText], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${label}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Disable Generate button when: no recording selected, no transcript, the
  // local component is busy with another op, OR backend is already generating
  // for THIS recording (state tracked in context, survives recording-switch).
  const canGenMom =
    !!currentRecordingId && rawText.trim().length > 0 && !busy && !isGenerating;
  const canGenSummary =
    !!currentMeetingId &&
    !!currentMeeting?.recordings.some((r) => r.mom_json) &&
    !busy;
  const canUpload = !!currentRecordingId && !busy;

  // ─── Empty state: no project selected at all ───
  // Show a clear CTA UI instead of the live transcript chrome —
  // recording without a project goes nowhere, and the textarea +
  // record buttons just confuse users in this state.
  if (!currentMeetingId) {
    return (
      <section className="pane pane-transcript pane-empty-no-project">
        <div className="empty-no-project-card">
          <div className="empty-no-project-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
          </div>
          <div className="empty-no-project-title">{t("transcriptPane.noProject.title")}</div>
          <div className="empty-no-project-text">
            {t("transcriptPane.noProject.body", { kbd: t("transcriptPane.createNewProject") })}
          </div>
          <div className="empty-no-project-actions">
            <button
              className="btn btn-primary btn-sm"
              type="button"
              disabled={creatingProject}
              onClick={() => { void ensureProjectAndRecording(); }}
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              <span>{creatingProject ? t("transcriptPane.creating") : t("transcriptPane.createNewProject")}</span>
            </button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="pane pane-transcript">
      {currentRecordingId && (
        <FloatingRail
          onGenerateMom={handleGenerateMom}
          hasMom={!!currentMeeting?.recordings.find((r) => r.id === currentRecordingId)?.mom_json}
        />
      )}
      <div className="pane-header pane-header-actions">
        <span className="pane-title">{t("pane.transcript")}</span>
        <div className="view-toggle">
          <button
            className={`view-toggle-btn${view === "raw" ? " active" : ""}`}
            type="button"
            onClick={() => switchView("raw")}
          >
            {t("view.raw")}
          </button>
          <button
            className={`view-toggle-btn${view === "clean" ? " active" : ""}`}
            type="button"
            onClick={() => switchView("clean")}
            title={
              cleanStatus === "running"
                ? t("transcriptPane.cleanRunning")
                : cleanStatus === "done"
                ? t("transcriptPane.cleanDone")
                : ""
            }
          >
            {t("view.clean")}
            {cleanStatus === "done" && view !== "clean" && (
              <span
                style={{ marginLeft: 4, fontSize: 10, color: "var(--accent)" }}
                aria-label={t("transcriptPane.cleanReady")}
              >
                ✓
              </span>
            )}
          </button>
        </div>
        <div className="pane-actions">
          <button
            className="btn btn-record btn-sm"
            type="button"
            onClick={handleStartRecord}
            disabled={!currentRecordingId || live.isRecording || busy}
            title={!currentRecordingId ? t("transcriptPane.pickMeetingFirst") : t("btn.record")}
          >
            <span className="rec-dot"></span>
            <span>{t("btn.record")}</span>
          </button>
          <button
            className="btn btn-stop btn-sm"
            type="button"
            onClick={handleStopRecord}
            disabled={!live.isRecording}
          >
            <svg viewBox="0 0 12 12" width="9" height="9">
              <rect x="2" y="2" width="8" height="8" fill="currentColor" />
            </svg>
            <span>{t("btn.stop")}</span>
          </button>
          <label
            className={`btn btn-outline btn-sm${canUpload ? "" : " disabled"}`}
            style={{ cursor: canUpload ? "pointer" : "not-allowed", opacity: canUpload ? 1 : 0.4 }}
            title={canUpload ? undefined : "Chọn 1 phiên họp trước"}
          >
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span>{t("btn.upload")}</span>
            <input
              ref={fileInputRef}
              type="file"
              accept="audio/*"
              hidden
              disabled={!canUpload}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
              }}
            />
          </label>
        </div>
      </div>

      <div className="pane-content">
        {status && (
          <div className={`pane-inline-status ${status.kind}`} aria-live="polite">
            {status.msg}
          </div>
        )}

        {overviewContent ? (
          overviewContent
        ) : view === "raw" ? (
          <textarea
            className="transcript-box"
            placeholder={t("transcriptPlaceholder")}
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
          />
        ) : streamingActive && rawSegments.length === 0 ? (
          // Loading placeholder shown ONLY until the first segment lands.
          // Once we have at least one block, NottaCleanView takes over —
          // it renders blocks as audio reaches them (block-by-block reveal),
          // while remaining segments continue to stream in the background.
          <div className="transcript-clean transcript-clean-loading">
            <div className="notta-loading">
              <div className="notta-loading-spinner" />
              <div className="notta-loading-title">
                {t("transcriptPane.processing.title")}
              </div>
              <div className="notta-loading-step">
                {status?.msg || t("transcriptPane.processing.fallback")}
              </div>
              <div className="notta-loading-hint">
                {t("transcriptPane.processing.hint")}
              </div>
            </div>
          </div>
        ) : (
          <div className="transcript-clean">
            {(() => {
              // Prefer rawSegments when they carry real timestamps
              // (faster-whisper path). Reason: the LLM cleaner has been
              // observed dropping segments + duplicating "Giai đoạn 4"-
              // style boilerplate even with the verbatim prompt, which
              // breaks positional pairing → most rows render `--:--` and
              // audio sync no longer matches the audio. Raw segments
              // already have speaker + time + word_ts directly from STT,
              // so trust them as the source of truth. cleanSegments only
              // wins when STT didn't produce timestamps at all (VNG MaaS).
              const rawHasTimestamps =
                rawSegments.length > 0 &&
                rawSegments.some((r) => r.start_ms != null);
              const rawAsClean = rawHasTimestamps
                ? rawSegments.map((r) => ({
                    speaker: r.speaker || "",
                    text: r.text || "",
                    tags: [],
                  }))
                : null;
              const segsToShow = rawAsClean ?? cleanSegs;

              if (segsToShow === null) {
                return (
                  <div className="transcript-clean-empty muted">
                    {busy ? t("transcriptPane.cleanLoading") : t("transcriptPane.cleanHint")}
                  </div>
                );
              }
              if (segsToShow.length === 0) {
                return (
                  <div className="transcript-clean-empty muted">
                    {t("transcriptPane.noSegments")}
                  </div>
                );
              }
              if (!currentRecordingId || !currentMeetingId) return null;
              return (
                <NottaCleanView
                  recordingId={currentRecordingId}
                  meetingId={currentMeetingId}
                  cleanSegments={segsToShow}
                  rawSegments={rawSegments}
                  clusterMapping={clusterMapping}
                  onRegenerate={regenerateClean}
                  onClusterMappingSaved={reloadClean}
                  onSegmentsChanged={reloadTranscript}
                  busy={busy}
                  // streaming=false here: per-word streaming animation is
                  // disabled because we now hide all output during the
                  // pipeline (loading placeholder above) and let karaoke
                  // alone drive the reveal once everything's ready.
                  streaming={false}
                  // Fire karaoke when a fresh upload's segments just
                  // landed. `freshUpload` flips true at the moment
                  // streamingActive flips false (see handleUpload) and
                  // back to false on user interaction with audio.
                  // NottaCleanView's own ref ensures it triggers exactly
                  // once per recording so re-renders don't re-arm.
                  autoPlayKaraoke={freshUpload}
                  // Scope speaker-chip dropdown to people marked as
                  // attendees on this recording. Empty list → fall back
                  // to all project members inside NottaCleanView.
                  attendees={
                    currentMeeting?.recordings
                      .find((r) => r.id === currentRecordingId)
                      ?.attendees?.map((a) => a.name)
                      .filter(Boolean) ?? []
                  }
                  // A guest name applied to a block but not a project member →
                  // append it to recording.attendees + reload so it becomes a
                  // reusable speaker option across all blocks.
                  onAddGuest={async (name) => {
                    if (!currentRecordingId) return;
                    const rec = currentMeeting?.recordings.find(
                      (r) => r.id === currentRecordingId,
                    );
                    const existing = rec?.attendees ?? [];
                    const dup = existing.some(
                      (a) =>
                        (a.name || "").trim().toLowerCase() ===
                        name.trim().toLowerCase(),
                    );
                    if (dup) return;
                    try {
                      await api.recordings.patch(currentRecordingId, {
                        attendees: [...existing, { name: name.trim() }],
                      });
                      await reloadCurrentMeeting();
                    } catch {
                      /* non-fatal — the speaker was already applied to the block */
                    }
                  }}
                />
              );
            })()}
          </div>
        )}

        <div className="pane-footer">
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={handleSaveTxt}
            disabled={!rawText.trim()}
          >
            {t("btn.saveTxt")}
          </button>
          {/* Background clean progress bar — only visible while running.
              Positioned between "Save .txt" and "Project summary" so it
              doesn't crowd the view toggle in the header. */}
          {cleanStatus === "running" && (
            <CleanProgressBar progress={cleanProgress} />
          )}
          <div className="spacer"></div>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={handleProjectSummary}
            disabled={!canGenSummary}
            title={
              canGenSummary
                ? t("btn.projectSummary")
                : t("transcriptPane.needRecordingsForSummary")
            }
          >
            {t("btn.projectSummary")}
          </button>
          <button
            className="btn btn-primary btn-sm"
            type="button"
            onClick={handleGenerateMom}
            disabled={!canGenMom}
            title={
              !currentRecordingId
                ? t("transcriptPane.pickMeetingFirst")
                : !rawText.trim()
                  ? t("transcriptPane.noTranscript")
                  : t("btn.generateMom")
            }
          >
            <span>{t("btn.generateMom")}</span>
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="5" y1="12" x2="19" y2="12" />
              <polyline points="12 5 19 12 12 19" />
            </svg>
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={noProjectModal !== null}
        title={t("transcriptPane.confirmNoProject.title")}
        message={t("transcriptPane.confirmNoProject.msg", { kbd: t("transcriptPane.createNewProject") })}
        confirmLabel={creatingProject ? t("transcriptPane.creating") : t("transcriptPane.confirmNoProject.confirm")}
        cancelLabel={t("transcriptPane.confirmNoProject.cancel")}
        accent
        onCancel={() => !creatingProject && setNoProjectModal(null)}
        onConfirm={() => { void confirmNoProjectAndProceed(); }}
      />
    </section>
  );
}

/** Background clean progress bar. Combines chunk progress (deterministic if
 * cleaner is mid-way through multi-chunk transcript) with a time-based
 * estimate (~45s per chunk for Qwen3-8B on a 14K-char chunk). Caps at 95%
 * until backend reports done so user doesn't see 100% then wait. */
function CleanProgressBar({
  progress,
}: {
  progress: {
    phase: "cleaning" | "saving";
    current_chunk: number;
    total_chunks: number;
    started_at_ms: number;
    raw_chars: number;
  } | null;
}) {
  const { t } = useApp();
  // Tick every 500ms so the time-based portion of the estimate animates.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 500);
    return () => window.clearInterval(id);
  }, []);

  if (!progress) {
    // Backend started a task but hasn't recorded progress yet (very first
    // moment) — indeterminate animated stripe.
    return (
      <div
        style={{
          marginLeft: 12,
          width: 160,
          height: 6,
          borderRadius: 4,
          background: "var(--surface-3)",
          overflow: "hidden",
          position: "relative",
        }}
        aria-label={t("transcriptPane.preparingClean")}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(90deg, transparent 0%, var(--accent) 50%, transparent 100%)",
            backgroundSize: "40% 100%",
            animation: "clean-stripe 1.2s linear infinite",
          }}
        />
        <style>{`@keyframes clean-stripe { 0% { background-position: -40% 0; } 100% { background-position: 140% 0; } }`}</style>
      </div>
    );
  }

  // Estimate based on chunk count + elapsed time. ~45s budget per chunk.
  const SEC_PER_CHUNK = 45;
  const elapsedSec = (Date.now() - progress.started_at_ms) / 1000;
  const totalBudget = Math.max(SEC_PER_CHUNK, SEC_PER_CHUNK * progress.total_chunks);
  // Time-based fraction, slowed at the tail so we don't hit 100% prematurely.
  const timePct = Math.min(95, (elapsedSec / totalBudget) * 100);
  // Chunk-based fraction (deterministic if cleaner has flushed progress).
  const chunkPct =
    progress.total_chunks > 0
      ? (progress.current_chunk / progress.total_chunks) * 100
      : 0;
  const pct = Math.max(timePct, chunkPct);
  const phaseLabel =
    progress.phase === "saving"
      ? "Đang lưu…"
      : progress.total_chunks > 1
      ? `Cleaning ${Math.min(progress.current_chunk + 1, progress.total_chunks)}/${progress.total_chunks}`
      : "Cleaning…";

  return (
    <div
      style={{
        marginLeft: 12,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
      title={`${Math.round(pct)}% · ${Math.round(elapsedSec)}s elapsed`}
    >
      <div
        style={{
          width: 140,
          height: 6,
          borderRadius: 4,
          background: "var(--surface-3)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: "var(--accent)",
            transition: "width 0.4s ease-out",
          }}
        />
      </div>
      <span style={{ fontSize: 10, color: "var(--text-mute)", whiteSpace: "nowrap" }}>
        {phaseLabel}
      </span>
    </div>
  );
}
