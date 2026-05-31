// ProjectOverview — shown when a project is selected but no specific recording
// is. Lists all recordings as clickable cards. Matches old #project-overview
// markup so legacy .po-* CSS applies directly.
import { useApp } from "../store/AppContext";

export function ProjectOverview() {
  const { currentMeeting, selectRecording, t } = useApp();
  if (!currentMeeting) return null;

  // Sort by started_at ASC — oldest first, newest at the bottom.
  const recordings = [...(currentMeeting.recordings || [])].sort((a, b) => {
    const ta = a.started_at ? Date.parse(a.started_at) : 0;
    const tb = b.started_at ? Date.parse(b.started_at) : 0;
    return ta - tb;
  });

  const fmtDate = (iso?: string | null) => {
    if (!iso) return "·";
    try {
      return new Date(iso).toLocaleString("vi-VN", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "·";
    }
  };

  if (recordings.length === 0) {
    return (
      <div className="project-overview">
        <div className="project-overview-empty muted">
          Project này chưa có phiên họp nào.
          <br />
          Bấm "+ Thêm phiên họp" trong sidebar để bắt đầu.
        </div>
      </div>
    );
  }

  return (
    <div className="project-overview">
      <div className="po-list">
        {recordings.map((r, idx) => (
          <div
            key={r.id}
            className="po-card"
            onClick={() => selectRecording(r.id)}
          >
            <div className="po-card-num">{idx + 1}</div>
            <div className="po-card-main">
              <div className="po-card-title">
                {r.session_label || `Recording ${idx + 1}`}
              </div>
              <div className="po-card-meta muted">
                {fmtDate(r.started_at)} · {r.segment_count || 0} {t("meta.seg")}
                {r.mom_json && (
                  <span style={{ marginLeft: 8, color: "var(--accent)" }}>
                    ✓ MoM
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
