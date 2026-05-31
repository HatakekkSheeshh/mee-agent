import { useState } from "react";
import { useApp } from "../store/AppContext";
import { api } from "../api/client";

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
  const att = currentMeeting?.attendees?.length || 0;
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
          <span>{currentMeeting?.date || t("meta.noDate")}</span>
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
          <span className="mono">00:00</span>
          <span className="mc-sub">· <span>0</span> {t("meta.seg")}</span>
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
          <div className="details-grid">
            <div className="field-block">
              <label>{t("details.date")}</label>
              <input type="date" className="field" defaultValue={currentMeeting?.date || ""} />
            </div>
            <div className="field-block">
              <label>{t("details.venue")}</label>
              <input type="text" className="field" placeholder={t("details.venuePlaceholder")} defaultValue={currentMeeting?.venue || ""} />
            </div>
            <div className="field-block" style={{ gridColumn: "1/-1" }}>
              <label>{t("details.purpose")}</label>
              <input type="text" className="field" placeholder={t("details.purposePlaceholder")} defaultValue={currentMeeting?.purpose || ""} />
            </div>
            <div className="field-block">
              <label>{t("details.chairedBy")}</label>
              <input type="text" className="field" placeholder={t("details.namePlaceholder")} defaultValue={currentMeeting?.chaired_by || ""} />
            </div>
            <div className="field-block">
              <label>{t("details.notedBy")}</label>
              <input type="text" className="field" placeholder={t("details.namePlaceholder")} defaultValue={currentMeeting?.noted_by || ""} />
            </div>
            <div className="field-block">
              <label>{t("details.attendees")}</label>
              <input
                type="text"
                className="field"
                placeholder={t("details.attendeesPlaceholder")}
                defaultValue={
                  currentMeeting?.attendees?.map((a) => a.name).join(", ") || ""
                }
              />
            </div>
            <div className="field-block">
              <label>{t("details.meetingLang")}</label>
              <select className="field" defaultValue="vi">
                <option value="vi">Tiếng Việt</option>
                <option value="en">English</option>
              </select>
              <div className="field-hint">{t("details.meetingLangHint")}</div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
