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
import type { CleanResponse } from "../types/api";

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
  } = useApp();
  const [view, setView] = useState<ViewMode>("raw");
  const [rawText, setRawText] = useState<string>("");
  const [cleanSegs, setCleanSegs] = useState<CleanResponse["clean_segments"] | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ─── Load transcript whenever the selected recording changes ───
  useEffect(() => {
    if (!currentRecordingId) {
      setRawText("");
      setCleanSegs(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const r = await api.recordings.transcript(currentRecordingId);
        if (cancelled) return;
        setRawText(r.transcript || "");
        setCleanSegs(null); // invalidate clean cache on recording switch
      } catch (e) {
        if (!cancelled) {
          setStatus({ kind: "error", msg: `Tải transcript lỗi: ${(e as Error).message}` });
        }
      }
    })();
    return () => { cancelled = true; };
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
      const { text } = await api.transcribe(file);
      if (!text?.trim()) {
        setStatus({ kind: "error", msg: "Không phát hiện giọng nói." });
        return;
      }
      setRawText(text);
      setStatus({ kind: "assessing", msg: "Đang lưu transcript vào DB…" });
      const imp = await api.meetings.importTranscript(currentMeetingId, {
        text,
        recording_id: currentRecordingId,
        replace: false,
      });
      await reloadCurrentMeeting();
      setStatus({
        kind: "success",
        msg: `Đã transcribe "${file.name}" ✓ (${imp.segments_count} đoạn)`,
      });
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
    setBusy(true);
    setMomStatus({ kind: "assessing", msg: "Đang tạo biên bản qua LangGraph…" });
    try {
      const currentText = rawText.trim();
      if (currentText) {
        await api.meetings.importTranscript(currentMeetingId, {
          text: currentText,
          recording_id: currentRecordingId,
          replace: false,
        });
      }
      const res = await api.recordings.generateMom(currentRecordingId);
      // Cache immediately — MoMPane displays without waiting for backend reload
      // (which would also require backend to be running latest code).
      setRecordingMom(currentRecordingId, res.notes);
      reloadCurrentMeeting(); // fire-and-forget refresh for sidebar/meta
      const memHint = res.memory_context_count
        ? ` (dùng ${res.memory_context_count} memory events)`
        : "";
      setMomStatus({ kind: "success", msg: `Đã tạo biên bản ✓${memHint}` });
      setTimeout(() => setMomStatus(null), 4000);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setMomStatus({ kind: "error", msg: `Lỗi tạo MoM: ${msg}` });
    } finally {
      setBusy(false);
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
        const r = await api.recordings.clean(currentRecordingId, false);
        setCleanSegs(r.clean_segments);
        setStatus({
          kind: "success",
          msg: r.cached ? "Clean (cache) ✓" : "Clean ✓",
        });
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setStatus({ kind: "error", msg: `Clean lỗi: ${msg}` });
        setView("raw");
      } finally {
        setBusy(false);
      }
    },
    [currentRecordingId, cleanSegs],
  );

  function regenerateClean() {
    if (!currentRecordingId) return;
    setBusy(true);
    setStatus({ kind: "assessing", msg: "Regenerate clean…" });
    api.recordings
      .clean(currentRecordingId, true)
      .then((r) => {
        setCleanSegs(r.clean_segments);
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

  const canGenMom = !!currentRecordingId && rawText.trim().length > 0 && !busy;
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
          >
            {t("view.clean")}
          </button>
        </div>
        <div className="pane-actions">
          <button
            className="btn btn-record btn-sm"
            type="button"
            disabled
            title="Live record — Phase B.2"
          >
            <span className="rec-dot"></span>
            <span>{t("btn.record")}</span>
          </button>
          <button className="btn btn-stop btn-sm" type="button" disabled>
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
            ) : (
              <>
                <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
                  <button
                    className="btn btn-ghost btn-xs"
                    type="button"
                    onClick={regenerateClean}
                    disabled={busy}
                  >
                    ↻ Regenerate
                  </button>
                </div>
                {cleanSegs.map((seg, i) => (
                  <div key={i} className="clean-block">
                    {seg.speaker && <div className="clean-speaker">{seg.speaker}</div>}
                    <div className="clean-text">{seg.text}</div>
                    {seg.tags && seg.tags.length > 0 && (
                      <div className="clean-tags">
                        {seg.tags.map((tag, j) => (
                          <span key={j} className={`tag tag-${tag}`}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
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
