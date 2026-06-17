import { useApp } from "../store/AppContext";
import { api } from "../api/client";

export function Topbar({ user: _user }: { user: { email: string; display_name: string | null } }) {
  const {
    toggleSidebar,
    t,
    currentMeeting,
    currentMeetingId,
    currentRecordingId,
    detailsOpen,
    toggleDetails,
    reloadMeetings,
    reloadCurrentMeeting,
  } = useApp();

  // ─── Meeting title + meta (Settings + User moved to Sidebar footer) ───
  const currentRec = currentMeeting?.recordings.find((r) => r.id === currentRecordingId);
  const titleValue = currentRec
    ? (currentRec.session_label || "")
    : (currentMeeting?.title || "");
  const titleKey = currentRec
    ? `rec-${currentRec.id}`
    : `meeting-${currentMeeting?.id || "none"}`;
  const placeholder = currentRec
    ? t("sidebar.recordingPlaceholder")
    : t("meeting.titlePlaceholder");

  const recordingForDetail = currentRec;
  const att = recordingForDetail?.attendees?.length || 0;

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

  async function saveTitle(next: string) {
    const trimmed = next.trim();
    if (trimmed === titleValue.trim()) return;
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
      alert(t("topbar.error.saveTitle", { msg: (e as Error).message }));
    }
  }

  const hasMeeting = !!currentMeetingId;

  return (
    <header className="topbar topbar-v2">
      <div className="tb-left">
        <button className="icon-btn" type="button" title="Sidebar" onClick={toggleSidebar}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>

        {hasMeeting && (
          <div className="topbar-title-block">
            <input
              type="text"
              className="topbar-title-input"
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
            {currentRecordingId && (
            <div className="topbar-meta">
              <span className="tb-meta-item" title="Ngày">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                  <line x1="16" y1="2" x2="16" y2="6" />
                  <line x1="8" y1="2" x2="8" y2="6" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
                <span>{recordingForDetail?.date || t("meta.noDate")}</span>
              </span>
              <span className="tb-meta-item">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                </svg>
                <span>
                  {att > 0 ? `${att} ${att === 1 ? t("meta.person") : t("meta.people")}` : t("meta.noPeople")}
                </span>
              </span>
              <span className="tb-meta-item">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10" />
                  <polyline points="12 6 12 12 16 14" />
                </svg>
                <span className="mono">{fmtDuration(totalDuration)}</span>
                <span className="tb-meta-sub">· {totalSegments} {t("meta.seg")}</span>
              </span>
            </div>
            )}
          </div>
        )}
      </div>

      <div className="tb-right">
        {hasMeeting && (
          <button
            className={`btn btn-ghost btn-sm tb-details-btn${detailsOpen ? " active" : ""}`}
            type="button"
            onClick={toggleDetails}
            title={t("btn.details")}
          >
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: detailsOpen ? "rotate(180deg)" : undefined, transition: "transform 150ms ease" }}>
              <polyline points="6 9 12 15 18 9" />
            </svg>
            <span>{t("btn.details")}</span>
          </button>
        )}
      </div>
    </header>
  );
}
