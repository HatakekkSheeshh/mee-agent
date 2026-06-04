import { useEffect, useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { api, type ModelProfile } from "../api/client";
import type { StringKey } from "../i18n";

// Look up a translated model label/description by profile id; fall back to
// whatever the backend sent (`p.label` / `p.description`) so new profiles
// added in model_registry.py work even without an i18n key yet.
function modelLabel(
  p: ModelProfile,
  t: (key: StringKey, vars?: Record<string, string | number>) => string,
): string {
  const key = `models.${p.id}.label` as StringKey;
  const translated = t(key);
  return translated === key ? p.label : translated;
}
function modelDescription(
  p: ModelProfile | undefined,
  t: (key: StringKey, vars?: Record<string, string | number>) => string,
): string {
  if (!p) return "";
  const key = `models.${p.id}.description` as StringKey;
  const translated = t(key);
  return translated === key ? p.description : translated;
}

export function MeetingControl() {
  const {
    currentMeeting,
    currentMeetingId,
    currentRecordingId,
    reloadMeetings,
    reloadCurrentMeeting,
    t,
  } = useApp();
  const [detailsOpen, setDetailsOpen] = useState(false);

  // ─── Detail fields: controlled inputs + debounced auto-save ───
  // Local state mirrors DB; user typing updates state immediately and a
  // 1.5s debounce sends a PATCH. Reloads from DB on meeting switch so the
  // panel is never out of sync.
  const [dPurpose, setDPurpose] = useState("");
  const [dVenue, setDVenue] = useState("");
  const [dDate, setDDate] = useState("");
  const [dChairedBy, setDChairedBy] = useState("");
  const [dNotedBy, setDNotedBy] = useState("");
  const [dAttendees, setDAttendees] = useState("");
  const [dVocab, setDVocab] = useState("");
  // Model picker — empty string = inherit (meeting → registry default).
  const [dStt, setDStt] = useState("");
  const [dLlm, setDLlm] = useState("");
  const [dMomLang, setDMomLang] = useState("");
  const [sttProfiles, setSttProfiles] = useState<ModelProfile[]>([]);
  const [llmProfiles, setLlmProfiles] = useState<ModelProfile[]>([]);
  const [defaultStt, setDefaultStt] = useState("");
  const [defaultLlm, setDefaultLlm] = useState("");
  const [detailsSaveState, setDetailsSaveState] =
    useState<"idle" | "saving" | "saved" | "error">("idle");

  // Load model profile list once on mount — endpoint is cheap + static.
  useEffect(() => {
    api.models.list()
      .then((r) => {
        setSttProfiles(r.stt);
        setLlmProfiles(r.llm);
        setDefaultStt(r.default_stt);
        setDefaultLlm(r.default_llm);
      })
      .catch(() => { /* ignore — fallback hardcoded defaults */ });
  }, []);
  const initSnapshotRef = useRef<string>("");
  const debounceRef = useRef<number | null>(null);

  // Per-meeting metadata moved to recordings in migration 0012. Detail panel
  // edits the SELECTED recording when one is selected; project-overview view
  // only edits vocab. Re-init local state on meeting OR recording change.
  const recordingForDetail = currentMeeting?.recordings.find(
    (r) => r.id === currentRecordingId,
  );

  useEffect(() => {
    const r = recordingForDetail;
    const m = currentMeeting;
    let dateStr = "";
    if (r?.date) {
      const s = String(r.date);
      dateStr = s.length >= 10 ? s.slice(0, 10) : "";
    }
    let attendeesStr = "";
    if (Array.isArray(r?.attendees)) {
      attendeesStr = (r!.attendees as unknown[])
        .map((a) =>
          typeof a === "string"
            ? a
            : a && typeof a === "object" && "name" in (a as object)
            ? String((a as { name?: string }).name || "")
            : ""
        )
        .filter(Boolean)
        .join(", ");
    }
    const next = {
      purpose: r?.purpose || "",
      venue: r?.venue || "",
      date: dateStr,
      chaired_by: r?.chaired_by || "",
      noted_by: r?.noted_by || "",
      attendees: attendeesStr,
      // dVocab maps to recording.vocab_hints when recording selected, else
      // meeting.vocab_hints (project default).
      vocab_hints: (r ? r.vocab_hints : m?.vocab_hints) || "",
      // Model picks — preselect the EFFECTIVE value: recording's own choice
      // (if set) → meeting default (project) → registry default. New
      // recordings auto-inherit from the previous sibling at create time
      // (see start_recording), so the value is rarely empty in practice.
      stt_model: (r ? (r.stt_model || m?.stt_model) : m?.stt_model) || "",
      llm_model: (r ? (r.llm_model || m?.llm_model) : m?.llm_model) || "",
      mom_language: (r ? (r.mom_language || m?.mom_language) : m?.mom_language) || "",
    };
    setDPurpose(next.purpose);
    setDVenue(next.venue);
    setDDate(next.date);
    setDChairedBy(next.chaired_by);
    setDNotedBy(next.noted_by);
    setDAttendees(next.attendees);
    setDVocab(next.vocab_hints);
    setDStt(next.stt_model);
    setDLlm(next.llm_model);
    setDMomLang(next.mom_language);
    initSnapshotRef.current = JSON.stringify(next);
    setDetailsSaveState("idle");
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
  }, [currentMeetingId, currentMeeting, currentRecordingId, recordingForDetail]);

  useEffect(() => {
    if (!currentMeetingId) return;
    const snapshot = JSON.stringify({
      purpose: dPurpose,
      venue: dVenue,
      date: dDate,
      chaired_by: dChairedBy,
      noted_by: dNotedBy,
      attendees: dAttendees,
      vocab_hints: dVocab,
      stt_model: dStt,
      llm_model: dLlm,
      mom_language: dMomLang,
    });
    if (snapshot === initSnapshotRef.current) return; // unchanged since last save
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(async () => {
      setDetailsSaveState("saving");
      try {
        if (recordingForDetail) {
          // Save to RECORDING — per-meeting-event metadata
          await api.recordings.patch(recordingForDetail.id, {
            purpose: dPurpose || null,
            venue: dVenue || null,
            date: dDate || null,
            chaired_by: dChairedBy || null,
            noted_by: dNotedBy || null,
            attendees: dAttendees
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean)
              .map((name) => ({ name })),
            vocab_hints: dVocab || null,
            stt_model: dStt || null,
            llm_model: dLlm || null,
            mom_language: dMomLang || null,
          } as never);
        } else {
          // Project overview → vocab + model defaults editable
          await api.meetings.patch(currentMeetingId, {
            vocab_hints: dVocab || null,
            stt_model: dStt || null,
            llm_model: dLlm || null,
            mom_language: dMomLang || null,
          } as never);
        }
        initSnapshotRef.current = snapshot;
        setDetailsSaveState("saved");
        reloadCurrentMeeting();
        window.setTimeout(() => setDetailsSaveState("idle"), 1500);
      } catch {
        setDetailsSaveState("error");
      }
    }, 1500);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dPurpose, dVenue, dDate, dChairedBy, dNotedBy, dAttendees, dVocab, dStt, dLlm, dMomLang, currentMeetingId, recordingForDetail]);
  const att = recordingForDetail?.attendees?.length || 0;
  const recCount = currentMeeting?.recordings.length || 0;
  const inOverview = !!currentMeeting && !currentRecordingId && recCount > 0;
  const recLabel = recCount === 1 ? t("meta.recording_one") : t("meta.recording_many");

  // Title input shows:
  //   - recording's session_label when a specific recording is selected
  //   - project (meeting) title when in overview / no recording
  const currentRec = currentMeeting?.recordings.find((r) => r.id === currentRecordingId);
  const titleValue = currentRec
    ? (currentRec.session_label || "")
    : (currentMeeting?.title || "");
  const titleKey = currentRec ? `rec-${currentRec.id}` : `meeting-${currentMeeting?.id || "none"}`;
  const placeholder = currentRec
    ? t("sidebar.recordingPlaceholder")
    : t("meeting.titlePlaceholder");

  // Duration + segment count from the selected recording (or summed across
  // recordings in project-overview mode).
  function fmtDuration(sec: number | null | undefined): string {
    if (!sec || sec <= 0) return "00:00";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (n: number) => String(n).padStart(2, "0");
    return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
  }
  const totalDuration = currentRec
    ? currentRec.duration_sec || 0
    : (currentMeeting?.recordings.reduce((sum, r) => sum + (r.duration_sec || 0), 0) || 0);
  const totalSegments = currentRec
    ? currentRec.segment_count || 0
    : (currentMeeting?.recordings.reduce((sum, r) => sum + (r.segment_count || 0), 0) || 0);

  // Save title on blur / Enter — PATCH recording.session_label OR meeting.title
  // depending on what's currently in focus.
  async function saveTitle(next: string) {
    const trimmed = next.trim();
    if (trimmed === titleValue.trim()) return; // no change
    try {
      if (currentRec) {
        await api.recordings.rename(currentRec.id, trimmed);
        await reloadCurrentMeeting();
      } else if (currentMeetingId) {
        await api.meetings.patch(currentMeetingId, { title: trimmed });
        await reloadMeetings();
        await reloadCurrentMeeting();
      }
    } catch (e) {
      alert(`Lưu tên lỗi: ${(e as Error).message}`);
    }
  }

  return (
    <section className="meeting-control">
      <div className="mc-row mc-title-row">
        <input
          type="text"
          className="title-input"
          placeholder={placeholder}
          defaultValue={titleValue}
          key={titleKey}
          onBlur={(e) => saveTitle(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur();
            if (e.key === "Escape") {
              (e.currentTarget as HTMLInputElement).value = titleValue;
              (e.currentTarget as HTMLInputElement).blur();
            }
          }}
        />
      </div>
      {inOverview && (
        <div className="mc-row mc-subtitle-row muted" style={{ fontSize: 13, marginTop: -4 }}>
          {recCount} {recLabel}
        </div>
      )}

      <div className="mc-row mc-meta-row">
        <span className="mc-meta-item">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
            <line x1="16" y1="2" x2="16" y2="6" />
            <line x1="8" y1="2" x2="8" y2="6" />
            <line x1="3" y1="10" x2="21" y2="10" />
          </svg>
          <span>{recordingForDetail?.date || t("meta.noDate")}</span>
        </span>
        <span className="mc-meta-item">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
          <span>
            {att > 0 ? `${att} ${att === 1 ? t("meta.person") : t("meta.people")}` : t("meta.noPeople")}
          </span>
        </span>
        <span className="mc-meta-item">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span className="mono">{fmtDuration(totalDuration)}</span>
          <span className="mc-sub">· <span>{totalSegments}</span> {t("meta.seg")}</span>
        </span>
        <button
          className="mc-meta-pill"
          type="button"
          onClick={() => setDetailsOpen((v) => !v)}
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
          {t("btn.details")}
        </button>
      </div>

      {detailsOpen && (
        <div className="details-panel">
          <div
            style={{
              marginBottom: 14,
              fontSize: 14,
              fontWeight: 500,
              fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
              letterSpacing: "0.01em",
              color: "var(--text-mute)",
            }}
          >
            {recordingForDetail
              ? t("details.editTitle", {
                  name:
                    recordingForDetail.title ||
                    recordingForDetail.session_label ||
                    t("details.thisRecording"),
                })
              : t("details.editProjectOverview")}
          </div>
          <div className="details-grid">
            <div className="field-block">
              <label>{t("details.date")}</label>
              <input
                type="date"
                className="field"
                value={dDate}
                onChange={(e) => setDDate(e.target.value)}
                disabled={!recordingForDetail}
              />
            </div>
            <div className="field-block">
              <label>{t("details.venue")}</label>
              <input
                type="text"
                className="field"
                placeholder={t("details.venuePlaceholder")}
                value={dVenue}
                onChange={(e) => setDVenue(e.target.value)}
                disabled={!recordingForDetail}
              />
            </div>
            <div className="field-block" style={{ gridColumn: "1/-1" }}>
              <label>{t("details.purpose")}</label>
              <input
                type="text"
                className="field"
                placeholder={t("details.purposePlaceholder")}
                value={dPurpose}
                onChange={(e) => setDPurpose(e.target.value)}
                disabled={!recordingForDetail}
              />
            </div>
            <div className="field-block">
              <label>{t("details.chairedBy")}</label>
              <input
                type="text"
                className="field"
                placeholder={t("details.namePlaceholder")}
                value={dChairedBy}
                onChange={(e) => setDChairedBy(e.target.value)}
                disabled={!recordingForDetail}
              />
            </div>
            <div className="field-block">
              <label>{t("details.notedBy")}</label>
              <input
                type="text"
                className="field"
                placeholder={t("details.namePlaceholder")}
                value={dNotedBy}
                onChange={(e) => setDNotedBy(e.target.value)}
                disabled={!recordingForDetail}
              />
            </div>
            <div className="field-block">
              <label>{t("details.attendees")}</label>
              <input
                type="text"
                className="field"
                placeholder={t("details.attendeesPlaceholder")}
                value={dAttendees}
                onChange={(e) => setDAttendees(e.target.value)}
                disabled={!recordingForDetail}
              />
              <div className="field-hint">{t("details.attendeesHint")}</div>
            </div>
            <div className="field-block" style={{ gridColumn: "1/-1" }}>
              <label>
                {t("details.vocabLabel")}{" "}
                {recordingForDetail
                  ? t("details.vocabRecordingHint")
                  : t("details.vocabProjectHint")}
              </label>
              <input
                type="text"
                className="field"
                placeholder={
                  recordingForDetail
                    ? t("details.vocabRecordingPlaceholder", {
                        projectVocab:
                          currentMeeting?.vocab_hints || t("details.noProjectVocab"),
                      })
                    : t("details.vocabProjectPlaceholder")
                }
                value={dVocab}
                onChange={(e) => setDVocab(e.target.value)}
              />
              <div className="field-hint">
                {recordingForDetail
                  ? t("details.vocabRecordingHelp")
                  : t("details.vocabProjectHelp")}
              </div>
            </div>
            <div className="field-block">
              <label>{t("details.sttModel")}</label>
              <select
                className="field"
                value={dStt || defaultStt}
                onChange={(e) => setDStt(e.target.value)}
              >
                {sttProfiles.map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.configured}>
                    {modelLabel(p, t)}
                    {!p.configured ? t("details.notConfigured") : ""}
                  </option>
                ))}
              </select>
              <div className="field-hint">
                {modelDescription(
                  sttProfiles.find((p) => p.id === (dStt || defaultStt)),
                  t,
                ) || t("details.sttDefaultHint")}
              </div>
            </div>
            <div className="field-block">
              <label>{t("details.llmModel")}</label>
              <select
                className="field"
                value={dLlm || defaultLlm}
                onChange={(e) => setDLlm(e.target.value)}
              >
                {llmProfiles.map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.configured}>
                    {modelLabel(p, t)}
                    {!p.configured ? t("details.notConfigured") : ""}
                  </option>
                ))}
              </select>
              <div className="field-hint">
                {modelDescription(
                  llmProfiles.find((p) => p.id === (dLlm || defaultLlm)),
                  t,
                ) || t("details.llmDefaultHint")}
              </div>
            </div>
            <div className="field-block">
              <label>{t("details.momLanguage")}</label>
              <select
                className="field"
                value={dMomLang}
                onChange={(e) => setDMomLang(e.target.value)}
              >
                <option value="">{t("details.momLanguageInherit")}</option>
                <option value="vi">{t("details.momLanguageVi")}</option>
                <option value="en">{t("details.momLanguageEn")}</option>
              </select>
              <div className="field-hint">{t("details.momLanguageHint")}</div>
            </div>
          </div>
          <div
            style={{
              marginTop: 8,
              fontSize: 11,
              color:
                detailsSaveState === "error"
                  ? "var(--danger)"
                  : detailsSaveState === "saved"
                  ? "var(--accent)"
                  : "var(--text-mute)",
            }}
          >
            {detailsSaveState === "saving" && t("details.saving")}
            {detailsSaveState === "saved" && t("details.saved")}
            {detailsSaveState === "error" && t("details.saveError")}
          </div>
        </div>
      )}
    </section>
  );
}
