// MoM pane — renders per-recording MoM (when a recording is selected) or
// project summary (when only a project is selected without a recording).
//
// Both data sources come from currentMeeting (loaded by AppContext). No local
// fetching here — when MoM is generated, TranscriptPane calls reloadCurrentMeeting
// and this pane re-renders automatically.
import { useRef, useState } from "react";
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
    setRecordingMom,
    reloadCurrentMeeting,
  } = useApp();
  const [busy, setBusy] = useState(false);
  // Per-pane "edit MoM" toggle — when on, every text field in the
  // structured MoMView becomes contenteditable in place. Edits debounce
  // 800ms then PATCH the whole mom_json so view ↔ edit toggle is
  // visually identical.
  const [momEditMode, setMomEditMode] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const saveTimerRef = useRef<number | null>(null);

  function handleMomPatch(updater: (prev: MoMJson) => MoMJson) {
    if (!currentRecordingId) return;
    // Mutate the in-memory cache so the next render shows the edit.
    // setRecordingMom triggers a re-render and the new value flows back
    // through `recordingMom` on the next pass.
    const prev = (currentRecordingId && freshRecordingMoms[currentRecordingId])
      || currentRec?.mom_json
      || null;
    if (!prev) return;
    const next = updater(prev);
    setRecordingMom(currentRecordingId, next);
    // Debounced save — multiple field edits in quick succession collapse
    // into a single PATCH.
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    setSaveState("saving");
    saveTimerRef.current = window.setTimeout(async () => {
      try {
        await api.recordings.patchMomJson(currentRecordingId, next);
        setSaveState("saved");
        window.setTimeout(() => setSaveState("idle"), 1500);
      } catch (e) {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        setMomStatus({ kind: "error", msg: t("momPane.error.save", { msg }) });
        setSaveState("error");
      }
    }, 800);
  }

  async function handleGenerateSummary() {
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


  const dlMd = currentRecordingId && recordingMom
    ? api.recordings.downloadUrl(currentRecordingId, "md")
    : null;

  function handleDownloadMd() {
    if (dlMd) window.open(dlMd, "_blank");
  }
  function handlePrintPdf() {
    // The print CSS targets #mom-result — that ID only renders when
    // MoMPane is mounted. Defensive: invoke print on next tick so the
    // pane is guaranteed in the DOM (MoMPane already renders when this
    // button is visible, but Ctrl+P from elsewhere might race).
    if (!recordingMom) return;
    window.setTimeout(() => window.print(), 0);
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
                  : t("momPane.needRecordings")
              }
            >
              {projectSummary ? t("momPane.updateSummary") : t("btn.projectSummary")}
            </button>
          )}
          {/* Status text + Re-generate + edit toggle moved to pane-footer.
           * Header now only has the per-mode action button + utility
           * icons (download / print). */}
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
      {/* Sub-header toolbar right below "Minutes" — mirrors the
       * Notta-style toolbar in the transcript pane (↻ Re-transcribe +
       * Chỉnh sửa toggle). Only renders when MoM exists. */}
      {!inOverview && recordingMom && currentRecordingId && (
        <div className="mom-toolbar">
          <button
            className="btn btn-ghost btn-xs"
            type="button"
            title={t("momPane.regenTitle")}
            onClick={() => {
              window.dispatchEvent(new CustomEvent("mee.regenerate-mom"));
            }}
            disabled={isBusy}
          >
            {t("momPane.regenBtn")}
          </button>
          <div className="mom-toolbar-hint">
            {momEditMode
              ? t("momPane.editHintOn")
              : t("momPane.editHintOff")}
          </div>
          <label
            className={`toggle-switch${momEditMode ? " on" : ""}`}
            title={
              momEditMode
                ? t("momPane.editToggleOn")
                : t("momPane.editToggleOff")
            }
          >
            <input
              type="checkbox"
              checked={momEditMode}
              onChange={(e) => setMomEditMode(e.target.checked)}
            />
            <span className="toggle-switch-track">
              <span className="toggle-switch-thumb" />
            </span>
            <span className="toggle-switch-label">{t("momPane.editLabel")}</span>
          </label>
        </div>
      )}

      <div className="pane-content">
        {momStatus && (
          <div className={`pane-inline-status ${momStatus.kind}`} aria-live="polite">
            {momStatus.msg}
          </div>
        )}
        {recordingMom && currentRecordingId ? (
          <>
            {momEditMode && (
              <div className="mom-save-banner">
                {saveState === "saving" && t("momPane.saving")}
                {saveState === "saved" && t("momPane.saved")}
                {saveState === "error" && t("momPane.saveError")}
                {saveState === "idle" && t("momPane.blurToSave")}
              </div>
            )}
            <MoMView
              mom={recordingMom}
              editMode={momEditMode}
              onPatch={handleMomPatch}
            />
          </>
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
  const { t } = useApp();
  return (
    <div className="mom-empty">
      <div className="mom-empty-icon" style={{ animation: "pulse 1.5s ease-in-out infinite" }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
      </div>
      <div className="mom-empty-title">{t("momPane.generatingTitle")}</div>
      <div className="mom-empty-text muted">{t("momPane.generatingBody")}</div>
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
//
// `editMode` makes every text field contenteditable in-place. The
// parent (MoMPane) owns the working copy of `mom` and the autosave
// debounce so a single PATCH carries all field edits per pause.
function MoMView({
  mom,
  editMode = false,
  onPatch,
}: {
  mom: MoMJson;
  editMode?: boolean;
  onPatch?: (updater: (prev: MoMJson) => MoMJson) => void;
}) {
  const { t } = useApp();
  const patch = onPatch || (() => {});

  const hasMeta = !!(mom.purpose || mom.date || mom.venue || mom.chaired_by
    || mom.noted_by || formatAttendees(mom.attendees));

  return (
    <div id="mom-result" className={editMode ? "mom-edit-mode" : ""}>
      {/* Meta — hide entirely when every field is empty (a wiped or
       * freshly-regenerated MoM); show in edit mode so the user can
       * fill blanks. */}
      {(hasMeta || editMode) && (
        <div className="mom-section">
          <div className="mom-section-title">{t("mom.section.info")}</div>
          <table className="mom-meta-table">
            <tbody>
              <EditableRow label={t("mom.section.purpose")} value={mom.purpose} editMode={editMode}
                onChange={(v) => patch((m) => ({ ...m, purpose: v }))} />
              <EditableRow label={t("mom.section.date")} value={mom.date} editMode={editMode}
                onChange={(v) => patch((m) => ({ ...m, date: v }))} />
              <EditableRow label={t("mom.section.venue")} value={mom.venue} editMode={editMode}
                onChange={(v) => patch((m) => ({ ...m, venue: v }))} />
              <EditableRow label={t("mom.section.chairedBy")} value={mom.chaired_by} editMode={editMode}
                onChange={(v) => patch((m) => ({ ...m, chaired_by: v }))} />
              <EditableRow label={t("mom.section.notedBy")} value={mom.noted_by} editMode={editMode}
                onChange={(v) => patch((m) => ({ ...m, noted_by: v }))} />
              {/* attendees is array-or-string; keep read-only for now */}
              <Row label={t("mom.section.attendees")} value={formatAttendees(mom.attendees)} />
            </tbody>
          </table>
        </div>
      )}

      {(mom.summary || editMode) && (
        <div className="mom-section">
          <div className="mom-section-title">{t("mom.section.summary")}</div>
          <div className="mom-summary">
            <Editable
              value={mom.summary || ""}
              editMode={editMode}
              onChange={(v) => patch((m) => ({ ...m, summary: v }))}
              placeholder={editMode ? t("momPane.placeholder.summary") : ""}
              multiline
            />
          </div>
        </div>
      )}

      {((mom.agenda_items && mom.agenda_items.length > 0) || editMode) && (
        <div className="mom-section">
          <div className="mom-section-title">{t("mom.section.agenda")}</div>
          {(mom.agenda_items || []).map((a, i) => (
            <div key={i} className="agenda-item">
              <div className="agenda-item-header">
                <span className="topic-no">{a.topic_no ?? i + 1}</span>
                <span className="agenda-title">
                  <Editable
                    value={a.agenda || ""}
                    editMode={editMode}
                    onChange={(v) => patch((m) => ({
                      ...m,
                      agenda_items: (m.agenda_items || []).map((x, j) =>
                        j === i ? { ...x, agenda: v } : x,
                      ),
                    }))}
                  />
                </span>
              </div>
              {(a.description || editMode) && (
                <div className="agenda-description">
                  <Editable
                    value={a.description || ""}
                    editMode={editMode}
                    onChange={(v) => patch((m) => ({
                      ...m,
                      agenda_items: (m.agenda_items || []).map((x, j) =>
                        j === i ? { ...x, description: v } : x,
                      ),
                    }))}
                    placeholder={editMode ? t("momPane.placeholder.desc") : ""}
                    multiline
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {((mom.action_items && mom.action_items.length > 0) || editMode) && (
        <div className="mom-section">
          <div className="mom-section-title">{t("mom.section.actionItems")}</div>
          <ActionItemsTable
            items={mom.action_items || []}
            editMode={editMode}
            onChange={(idx, field, v) => patch((m) => ({
              ...m,
              action_items: (m.action_items || []).map((x, j) =>
                j === idx ? { ...x, [field]: v } : x,
              ),
            }))}
          />
        </div>
      )}

      {((mom.decisions && mom.decisions.length > 0) || editMode) && (
        <BulletSection
          title={t("mom.section.decisions")}
          items={mom.decisions || []}
          editMode={editMode}
          onChange={(idx, v) => patch((m) => ({
            ...m,
            decisions: (m.decisions || []).map((x, j) =>
              j === idx ? (typeof x === "string" ? v : { ...x, text: v }) : x,
            ),
          }))}
        />
      )}
      {((mom.commitments && mom.commitments.length > 0) || editMode) && (
        <BulletSection
          title={t("mom.section.commitments")}
          items={mom.commitments || []}
          editMode={editMode}
          onChange={(idx, v) => patch((m) => ({
            ...m,
            commitments: (m.commitments || []).map((x, j) =>
              j === idx ? (typeof x === "string" ? v : { ...x, text: v }) : x,
            ),
          }))}
        />
      )}
      {((mom.blockers && mom.blockers.length > 0) || editMode) && (
        <BulletSection
          title={t("mom.section.blockers")}
          items={mom.blockers || []}
          editMode={editMode}
          onChange={(idx, v) => patch((m) => ({
            ...m,
            blockers: (m.blockers || []).map((x, j) =>
              j === idx ? (typeof x === "string" ? v : { ...x, text: v }) : x,
            ),
          }))}
        />
      )}
    </div>
  );
}

// Inline contenteditable text. Read-only span when editMode=false so
// the original layout is byte-identical. On blur we emit onChange only
// if the trimmed text actually differs — protects against a phantom
// PATCH per field every time the user clicks through.
function Editable({
  value,
  editMode,
  onChange,
  placeholder = "",
  multiline = false,
}: {
  value: string;
  editMode: boolean;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
}) {
  if (!editMode) {
    return <>{value}</>;
  }
  return (
    <span
      className={`editable-inline${multiline ? " multiline" : ""}`}
      contentEditable
      suppressContentEditableWarning
      data-placeholder={placeholder}
      onBlur={(e) => {
        const next = (e.currentTarget.textContent || "").trim();
        if (next !== (value || "").trim()) onChange(next);
      }}
    >
      {value}
    </span>
  );
}

function EditableRow({
  label, value, editMode, onChange,
}: {
  label: string;
  value?: string | null;
  editMode: boolean;
  onChange: (v: string) => void;
}) {
  const { t } = useApp();
  if (!value && !editMode) return null;
  return (
    <tr>
      <td>{label}</td>
      <td>
        <Editable
          value={value || ""}
          editMode={editMode}
          onChange={onChange}
          placeholder={editMode ? t("momPane.placeholder.empty") : ""}
        />
      </td>
    </tr>
  );
}

/** Coerce mom.attendees (string OR array of attendee objects OR null) into
 * a display string. Without this, rendering an object array as a React child
 * throws "Objects are not valid as a React child" and unmounts the pane. */
function formatAttendees(
  v: string | { name?: string; title?: string; department?: string }[] | null | undefined,
): string {
  if (!v) return "";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    return v
      .map((a) =>
        typeof a === "string"
          ? a
          : a && typeof a === "object"
          ? String(a.name || "").trim()
          : "",
      )
      .filter(Boolean)
      .join(", ");
  }
  return "";
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

// Recognise the VN placeholder LLM emits when no deadline is mentioned so we
// can render it in whatever language the UI is currently in. The prompt's
// schema example still has "Chưa xác định" hardcoded, so even when output
// language is English the LLM may copy that string verbatim.
const VN_DEADLINE_TBD = /^chưa\s*xác\s*định$/i;

function ActionItemsTable({
  items,
  editMode = false,
  onChange,
}: {
  items: ActionItem[];
  editMode?: boolean;
  onChange?: (idx: number, field: "item" | "pic" | "deadline", v: string) => void;
}) {
  const { t } = useApp();
  // Group ALL items by PIC (not just consecutive), preserving:
  //   - the order PICs first appear in the LLM output
  //   - the order of items within each PIC
  // Tasks without `item` text are dropped (LLM occasionally emits {pic, deadline}
  // with no task description — see Meeting 3's old mom_json).
  // In edit mode the PIC-grouping breaks the index-to-original mapping,
  // so render the raw list directly (one row per item, original index
  // preserved). In view mode keep the nice grouped layout.
  const groups = (() => {
    const order: string[] = [];
    const byPic = new Map<string, { ai: ActionItem; idx: number }[]>();
    items.forEach((ai, idx) => {
      if (!ai.item || !ai.item.trim()) return;
      const pic = (ai.pic || "—").trim() || "—";
      if (!byPic.has(pic)) {
        byPic.set(pic, []);
        order.push(pic);
      }
      byPic.get(pic)!.push({ ai, idx });
    });
    return order.map((pic) => ({ pic, tasks: byPic.get(pic)! }));
  })();

  if (editMode) {
    return (
      <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", overflow: "hidden" }}>
        {items.map((ai, idx) => (
          <div key={idx} className="action-item">
            <span className="action-pic">
              <Editable
                value={ai.pic || ""}
                editMode
                onChange={(v) => onChange?.(idx, "pic", v)}
                placeholder="PIC"
              />
            </span>
            <span className="action-task">
              <Editable
                value={ai.item || ""}
                editMode
                onChange={(v) => onChange?.(idx, "item", v)}
                placeholder={t("momPane.placeholder.taskDesc")}
              />
            </span>
            <span className="action-deadline">
              <Editable
                value={ai.deadline || ""}
                editMode
                onChange={(v) => onChange?.(idx, "deadline", v)}
                placeholder="Deadline"
              />
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", overflow: "hidden" }}>
      {groups.flatMap((g) =>
        g.tasks.map(({ ai }, j) => (
          <div
            key={`${g.pic}-${j}`}
            className={`action-item${j > 0 ? " merged" : ""}`}
          >
            <span className="action-pic">{j === 0 ? g.pic : ""}</span>
            <span className="action-task">{ai.item}</span>
            <span className="action-deadline">
              {ai.deadline && VN_DEADLINE_TBD.test(ai.deadline.trim())
                ? t("mom.deadlineTbd")
                : ai.deadline || ""}
            </span>
          </div>
        )),
      )}
    </div>
  );
}

function BulletSection({
  title,
  items,
  editMode = false,
  onChange,
}: {
  title: string;
  items: (string | { text: string; by?: string })[];
  editMode?: boolean;
  onChange?: (idx: number, text: string) => void;
}) {
  return (
    <div className="mom-section">
      <div className="mom-section-title">{title}</div>
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        {items.map((it, i) => {
          const text = typeof it === "string" ? it : it.text;
          const by = typeof it === "string" ? undefined : it.by;
          return (
            <li key={i}>
              <Editable
                value={text}
                editMode={editMode}
                onChange={(v) => onChange?.(i, v)}
              />
              {by && <span className="muted small"> — {by}</span>}
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
