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
import { useLiveRecording, type LiveSegment } from "../hooks/useLiveRecording";

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
  const [busy, setBusy] = useState(false);
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
        msg: "Đang tạo biên bản qua LangGraph… (tiếp tục từ lần bấm trước)",
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
  const live = useLiveRecording({
    uid: currentRecordingId || "",
    language: "vi",
    onSegments: onLiveSegments,
    onStatus: onLiveStatus,
  });

  async function handleStartRecord() {
    if (!currentRecordingId) {
      alert("Chọn 1 phiên họp trước");
      return;
    }
    // Snapshot current text BEFORE starting — new live segments will be
    // appended below it (record/stop/record again keeps prior content).
    baseTextRef.current = rawText;
    completedRef.current = [];
    await live.start();
  }

  async function handleStopRecord() {
    live.stop();
    // After stop, persist accumulated text to DB so Clean + Generate MoM can read it.
    if (currentMeetingId && currentRecordingId && rawText.trim()) {
      // Immediate feedback — diarize runs in the WS server (post_record_diarize)
      // and clean runs in the API server (clean_orchestrator). Both kick off
      // here. The cleanStatus poller below picks up the LLM phase; user sees
      // the speaker tags appear when /clean refresh fires.
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
        pingCleanStatus();  // import_transcript triggered background clean
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
          setStatus({ kind: "error", msg: `Tải transcript lỗi: ${(e as Error).message}` });
        }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRecordingId]);

  // ─── Upload audio file ───
  async function handleUpload(file: File) {
    if (!currentRecordingId) {
      alert("Chọn 1 phiên họp trước (sidebar → click phiên hoặc + Thêm phiên).");
      return;
    }
    if (!currentMeetingId) return;
    setBusy(true);
    setStatus({ kind: "assessing", msg: `Đang upload "${file.name}"…` });
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
      const transcribeResp = await api.transcribe(
        file, "vi", vocabStr, attendeesStr, currentRecordingId || "",
      );
      const text = transcribeResp.text;
      if (!text?.trim()) {
        setStatus({ kind: "error", msg: "Không phát hiện giọng nói." });
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
      setStatus({ kind: "assessing", msg: "Đang lưu transcript vào DB…" });
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
                    msg: "Đã phân tích speakers xong ✓",
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
      setStatus({ kind: "error", msg: `Lỗi: ${msg}` });
    } finally {
      setBusy(false);
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
    setMomStatus({ kind: "assessing", msg: "Đang tạo biên bản qua LangGraph…" });
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
      let memoryCount = 0;
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
            memoryCount = status.result?.memory_context_count ?? 0;
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
        memoryCount = res.memory_context_count;
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
        const memHint = memoryCount ? ` (dùng ${memoryCount} memory events)` : "";
        setMomStatus({ kind: "success", msg: `Đã tạo biên bản ✓${memHint}` });
        setTimeout(() => setMomStatus(null), 4000);
      }
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      if (stillOnThis()) {
        setMomStatus({ kind: "error", msg: `Lỗi tạo MoM: ${msg}` });
      }
    } finally {
      unmarkGeneratingRecording(targetRid);
    }
  }

  // ─── Generate project-level summary ───
  async function handleProjectSummary() {
    if (!currentMeetingId) return;
    setBusy(true);
    setMomStatus({ kind: "assessing", msg: "Đang tổng kết project…" });
    try {
      const res = await api.meetings.generateProjectSummary(currentMeetingId);
      setProjectSummary(currentMeetingId, res.summary);
      reloadCurrentMeeting();
      setMomStatus({
        kind: "success",
        msg: `Đã tổng kết ${res.summary.session_count} phiên ✓`,
      });
      setTimeout(() => setMomStatus(null), 4000);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setMomStatus({ kind: "error", msg: `Lỗi tổng kết: ${msg}` });
    } finally {
      setBusy(false);
    }
  }

  // ─── Switch view: fetch clean segments lazily ───
  const switchView = useCallback(
    async (next: ViewMode) => {
      setView(next);
      if (next !== "clean" || !currentRecordingId || cleanSegs !== null) return;
      setBusy(true);
      setStatus({ kind: "assessing", msg: "Đang clean transcript (LLM)…" });
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
        setStatus({ kind: "assessing", msg: "Đang clean transcript…" });
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
        setStatus({ kind: "error", msg: `Clean lỗi: ${msg}` });
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
    setStatus({ kind: "assessing", msg: "Regenerate clean (LLM ~2-4 phút)…" });
    requestClean(currentRecordingId, true)
      .then((r) => {
        setCleanSegs(r.clean_segments || []);
        setEditedHtml(r.edited_html || null);
        setClusterMapping(r.cluster_mapping || {});
        setPreMappedClusters(r.pre_mapped_clusters || []);
        setAvailableClusters(r.available_clusters || []);
        setStatus({ kind: "success", msg: "Clean ✓" });
      })
      .catch((e) => {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setStatus({ kind: "error", msg: `Lỗi: ${msg}` });
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

  return (
    <section className="pane pane-transcript">
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
                ? "Clean đang được tạo ngầm — bấm sẽ đợi LLM xong (~30s-2min)"
                : cleanStatus === "done"
                ? "Clean đã sẵn sàng — bấm để xem instant từ DB"
                : ""
            }
          >
            {t("view.clean")}
            {cleanStatus === "done" && view !== "clean" && (
              <span
                style={{ marginLeft: 4, fontSize: 10, color: "var(--accent)" }}
                aria-label="Clean sẵn sàng"
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
            title={!currentRecordingId ? "Chọn 1 phiên họp trước" : t("btn.record")}
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
        ) : (
          <div className="transcript-clean">
            {cleanSegs === null ? (
              <div className="transcript-clean-empty muted">
                {busy ? "Đang clean…" : "Bấm tab Clean để LLM format lại."}
              </div>
            ) : cleanSegs.length === 0 ? (
              <div className="transcript-clean-empty muted">Không có segment nào.</div>
            ) : currentRecordingId && currentMeetingId ? (
              <NottaCleanView
                recordingId={currentRecordingId}
                meetingId={currentMeetingId}
                cleanSegments={cleanSegs}
                rawSegments={rawSegments}
                clusterMapping={clusterMapping}
                onRegenerate={regenerateClean}
                onClusterMappingSaved={reloadClean}
                busy={busy}
              />
            ) : null}
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
                : "Cần ít nhất 1 phiên đã có biên bản trong project"
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
                ? "Chọn 1 phiên họp trước"
                : !rawText.trim()
                  ? "Chưa có transcript"
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
        aria-label="Đang chuẩn bị clean"
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
