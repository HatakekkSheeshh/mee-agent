// MoM pane — renders per-recording MoM (when a recording is selected) or
// project summary (when only a project is selected without a recording).
//
// Both data sources come from currentMeeting (loaded by AppContext). No local
// fetching here — when MoM is generated, TranscriptPane calls reloadCurrentMeeting
// and this pane re-renders automatically.
import { useState } from "react";
import { useApp } from "../store/AppContext";
import { api, ApiError } from "../api/client";
import type { MoMJson, ProjectSummary, ActionItem } from "../types/api";

export function MoMPane() {
  const {
    t,
    currentMeeting,
    currentMeetingId,
    currentRecordingId,
    momStatus,
    setMomStatus,
    freshRecordingMoms,
    freshProjectSummary,
    setProjectSummary,
    reloadCurrentMeeting,
  } = useApp();
  const [busy, setBusy] = useState(false);

  async function handleGenerateSummary() {
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

  // Decide what to show:
  //   - recording selected → fresh MoM from cache OR backend response
  //   - else → fresh project summary OR backend's stored summary OR empty
  // The fresh-cache takes precedence so a just-generated MoM displays
  // immediately, even if the backend's /meetings/{id} response hasn't been
  // updated yet to include recording.mom_json.
  const currentRec = currentMeeting?.recordings.find((r) => r.id === currentRecordingId);
  const recordingMom: MoMJson | null =
    (currentRecordingId && freshRecordingMoms[currentRecordingId]) ||
    currentRec?.mom_json ||
    null;
  const projectSummary: ProjectSummary | null =
    (currentMeetingId && freshProjectSummary[currentMeetingId]) ||
    (currentMeeting?.project_summary_json as ProjectSummary | undefined) ||
    null;
  const showSummary = !currentRecordingId && projectSummary;
  const isBusy = momStatus?.kind === "assessing" || busy;
  const inOverview = !!currentMeetingId && !currentRecordingId;
  const canGenSummary =
    !!currentMeetingId &&
    !!currentMeeting?.recordings.some((r) => r.mom_json) &&
    !isBusy;

  const statusText = recordingMom || showSummary ? t("mom.generated") : t("mom.empty");

  const dlMd = currentRecordingId && recordingMom
    ? api.recordings.downloadUrl(currentRecordingId, "md")
    : null;

  function handleDownloadMd() {
    if (dlMd) window.open(dlMd, "_blank");
  }
  function handlePrintPdf() {
    if (recordingMom) window.print();
  }

  return (
    <section className="pane pane-mom">
      <div className="pane-header">
        <span className="pane-title">{t("pane.minutes")}</span>
        <div className="pane-meta">
          {inOverview && (
            <button
              className="btn btn-primary btn-xs"
              type="button"
              onClick={handleGenerateSummary}
              disabled={!canGenSummary}
              title={
                canGenSummary
                  ? t("btn.projectSummary")
                  : "Cần ít nhất 1 phiên đã có biên bản"
              }
            >
              {projectSummary ? "↻ Cập nhật tổng kết" : t("btn.projectSummary")}
            </button>
          )}
          {!inOverview && <span className="mono small">{statusText}</span>}
          <button
            className="icon-btn icon-btn-sm"
            type="button"
            onClick={handleDownloadMd}
            disabled={!dlMd}
            title={t("tip.downloadMd")}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </button>
          <button
            className="icon-btn icon-btn-sm"
            type="button"
            onClick={handlePrintPdf}
            disabled={!recordingMom}
            title={t("tip.downloadPdf")}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 6 2 18 2 18 9" />
              <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" />
              <rect x="6" y="14" width="12" height="8" />
            </svg>
          </button>
        </div>
      </div>
      <div className="pane-content">
        {momStatus && (
          <div className={`pane-inline-status ${momStatus.kind}`} aria-live="polite">
            {momStatus.msg}
          </div>
        )}
        {recordingMom ? (
          <MoMView mom={recordingMom} />
        ) : showSummary && projectSummary ? (
          <ProjectSummaryView summary={projectSummary} />
        ) : isBusy ? (
          <LoadingState />
        ) : (
          <EmptyState t={t} />
        )}
      </div>
    </section>
  );
}

// ─── Loading state ─────────────────────────────────────────────────
function LoadingState() {
  return (
    <div className="mom-empty">
      <div className="mom-empty-icon" style={{ animation: "pulse 1.5s ease-in-out infinite" }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
      </div>
      <div className="mom-empty-title">Đang tạo biên bản…</div>
      <div className="mom-empty-text muted">LLM đang xử lý — vài giây nữa sẽ xong.</div>
    </div>
  );
}

// ─── Empty state ───────────────────────────────────────────────────
function EmptyState({ t }: { t: (k: import("../i18n").StringKey) => string }) {
  return (
    <div className="mom-empty">
      <div className="mom-empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="16" y1="13" x2="8" y2="13" />
          <line x1="16" y1="17" x2="8" y2="17" />
          <line x1="10" y1="9" x2="8" y2="9" />
        </svg>
      </div>
      <div className="mom-empty-title">{t("mom.emptyTitle")}</div>
      <div
        className="mom-empty-text"
        dangerouslySetInnerHTML={{
          __html: t("mom.emptyText").replace(
            "{kbd}",
            `<span class="kbd">${t("btn.generateMom")}</span>`,
          ),
        }}
      />
    </div>
  );
}

// ─── Per-recording MoM ─────────────────────────────────────────────
function MoMView({ mom }: { mom: MoMJson }) {
  return (
    <div id="mom-result">
      {/* Meta */}
      <div className="mom-section">
        <div className="mom-section-title">Thông tin cuộc họp</div>
        <table className="mom-meta-table">
          <tbody>
            <Row label="Mục đích / Purpose" value={mom.purpose} />
            <Row label="Ngày" value={mom.date} />
            <Row label="Địa điểm" value={mom.venue} />
            <Row label="Chủ trì" value={mom.chaired_by} />
            <Row label="Người ghi" value={mom.noted_by} />
            <Row label="Người tham dự" value={mom.attendees} />
          </tbody>
        </table>
      </div>

      {mom.summary && (
        <div className="mom-section">
          <div className="mom-section-title">Tóm tắt</div>
          <div className="mom-summary">{mom.summary}</div>
        </div>
      )}

      {mom.agenda_items && mom.agenda_items.length > 0 && (
        <div className="mom-section">
          <div className="mom-section-title">Nội dung cuộc họp</div>
          {mom.agenda_items.map((a, i) => (
            <div key={i} className="agenda-item">
              <div className="agenda-item-header">
                <span className="topic-no">{a.topic_no ?? i + 1}</span>
                <span className="agenda-title">{a.agenda}</span>
              </div>
              {a.description && <div className="agenda-description">{a.description}</div>}
            </div>
          ))}
        </div>
      )}

      {mom.action_items && mom.action_items.length > 0 && (
        <div className="mom-section">
          <div className="mom-section-title">Action items</div>
          <ActionItemsTable items={mom.action_items} />
        </div>
      )}

      {mom.decisions && mom.decisions.length > 0 && (
        <BulletSection title="Quyết định" items={mom.decisions} />
      )}
      {mom.commitments && mom.commitments.length > 0 && (
        <BulletSection title="Cam kết" items={mom.commitments} />
      )}
      {mom.blockers && mom.blockers.length > 0 && (
        <BulletSection title="Blockers" items={mom.blockers} />
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <tr>
      <td>{label}</td>
      <td>{value}</td>
    </tr>
  );
}

function ActionItemsTable({ items }: { items: ActionItem[] }) {
  // Merge consecutive items with same PIC (per user pref from past memory).
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", overflow: "hidden" }}>
      {items.map((ai, i) => {
        const prev = i > 0 ? items[i - 1] : null;
        const sameAsPrev = prev && prev.pic && prev.pic === ai.pic;
        return (
          <div key={i} className={`action-item${sameAsPrev ? " merged" : ""}`}>
            <span className="action-pic">{sameAsPrev ? "" : ai.pic || "—"}</span>
            <span className="action-task">{ai.item}</span>
            <span className="action-deadline">{ai.deadline || ""}</span>
          </div>
        );
      })}
    </div>
  );
}

function BulletSection({
  title,
  items,
}: {
  title: string;
  items: (string | { text: string; by?: string })[];
}) {
  return (
    <div className="mom-section">
      <div className="mom-section-title">{title}</div>
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {items.map((it, i) => {
          if (typeof it === "string") return <li key={i}>{it}</li>;
          return (
            <li key={i}>
              {it.text}
              {it.by && <span className="muted small"> — {it.by}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ─── Project summary view ──────────────────────────────────────────
function ProjectSummaryView({ summary }: { summary: ProjectSummary }) {
  const [showRaw, setShowRaw] = useState(false);
  return (
    <div id="mom-result">
      <div className="mom-section">
        <div className="mom-section-title">
          📊 Tổng kết project: {summary.project_title}
        </div>
        <div className="muted small" style={{ marginBottom: 12 }}>
          {summary.session_count} phiên họp · Tạo lúc:{" "}
          {(summary.generated_at || "").slice(0, 19).replace("T", " ")}
        </div>

        {summary.narrative && (
          <div className="mom-summary" style={{ marginBottom: 16 }}>
            {summary.narrative}
          </div>
        )}

        <div className="mom-section-title" style={{ marginTop: 16 }}>
          ⏱ Timeline quyết định
        </div>
        {summary.decisions_timeline.length === 0 ? (
          <div className="muted">Chưa có quyết định nào trong project.</div>
        ) : (
          summary.decisions_timeline.map((entry) => {
            const dt = (entry.date || "").slice(0, 10);
            return (
              <div
                key={entry.recording_id}
                style={{
                  borderLeft: "2px solid var(--accent)",
                  paddingLeft: 14,
                  marginBottom: 14,
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13 }}>
                  {dt} — {entry.session_label}
                </div>
                <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
                  {entry.decisions.map((d, j) => (
                    <li key={j} style={{ margin: "3px 0", fontSize: 13 }}>
                      {d}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        )}

        <div style={{ marginTop: 16 }}>
          <button
            className="btn btn-ghost btn-xs"
            type="button"
            onClick={() => setShowRaw((v) => !v)}
          >
            {showRaw ? "Ẩn JSON" : "Xem JSON raw"}
          </button>
          {showRaw && (
            <pre
              className="mono small"
              style={{
                marginTop: 8,
                padding: 12,
                background: "var(--surface-2)",
                borderRadius: "var(--r-sm)",
                overflow: "auto",
              }}
            >
              {JSON.stringify(summary, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
