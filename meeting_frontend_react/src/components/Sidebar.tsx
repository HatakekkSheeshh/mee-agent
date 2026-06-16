import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../store/AppContext";
import { api } from "../api/client";
import { PromptDialog } from "./PromptDialog";
import { UserControls } from "./UserControls";

export function Sidebar({ user }: { user: { email: string; display_name: string | null } }) {
  const {
    meetings,
    meetingsLoading,
    currentMeetingId,
    currentMeeting,
    currentRecordingId,
    selectMeeting,
    selectRecording,
    reloadMeetings,
    reloadCurrentMeeting,
    confirm,
    sidebarOpen,
    toggleSidebar,
    t,
  } = useApp();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Project row context menu: which project id is open + screen position.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });

  // Inline rename state (project)
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Centered modal for new-project input (replaces native window.prompt).
  const [creatingProject, setCreatingProject] = useState(false);

  function startNewProject() {
    setCreatingProject(true);
  }
  async function commitNewProject(title: string) {
    setCreatingProject(false);
    if (!title.trim()) return;
    try {
      const m = await api.meetings.create({ title: title.trim() });
      await reloadMeetings();
      await selectMeeting(m.id);
      setExpanded((s) => new Set(s).add(m.id));
    } catch (e) {
      alert(t("sidebar.error.createProject", { msg: (e as Error).message }));
    }
  }

  async function handleNewRecording(meetingId: string) {
    const existingCount = currentMeeting?.recordings.length || 0;
    const label = t("sidebar.meetingDefault", { n: existingCount + 1 });
    try {
      const r = await api.recordings.create(meetingId, label);
      await reloadCurrentMeeting();
      selectRecording(r.id);
    } catch (e) {
      alert(t("sidebar.error.createRecording", { msg: (e as Error).message }));
    }
  }

  function toggle(id: string) {
    setExpanded((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // ─── Project row menu actions ───
  async function handlePin(m: { id: string; is_pinned?: boolean }) {
    setMenuFor(null);
    try {
      await api.meetings.patch(m.id, { is_pinned: !m.is_pinned });
      await reloadMeetings();
    } catch (e) {
      alert(t("sidebar.error.generic", { msg: (e as Error).message }));
    }
  }

  function startRename(m: { id: string; title: string }) {
    setMenuFor(null);
    setRenamingId(m.id);
    setRenameValue(m.title || "");
  }

  async function commitRename() {
    if (!renamingId) return;
    const value = renameValue.trim();
    if (!value) {
      setRenamingId(null);
      return;
    }
    try {
      await api.meetings.patch(renamingId, { title: value });
      await reloadMeetings();
      if (currentMeetingId === renamingId) await reloadCurrentMeeting();
    } catch (e) {
      alert(t("sidebar.error.rename", { msg: (e as Error).message }));
    } finally {
      setRenamingId(null);
    }
  }

  async function handleDeleteProject(meetingId: string, title: string) {
    setMenuFor(null);
    const ok = await confirm({
      title: t("confirm.deleteProject.title"),
      message: `${t("confirm.deleteProject.msg")}\n\n"${title || t("sidebar.untitledProject")}"`,
      confirmLabel: t("confirm.delete"),
      cancelLabel: t("confirm.cancel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api.meetings.remove(meetingId);
      if (currentMeetingId === meetingId) selectMeeting(null);
      await reloadMeetings();
    } catch (e) {
      alert(t("sidebar.error.delete", { msg: (e as Error).message }));
    }
  }

  async function handleDeleteRecording(recordingId: string, label: string) {
    const ok = await confirm({
      title: t("confirm.deleteRecording.title"),
      message: `${t("confirm.deleteRecording.msg")}\n\n"${label || t("sidebar.recordingPlaceholder")}"`,
      confirmLabel: t("confirm.delete"),
      cancelLabel: t("confirm.cancel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api.recordings.remove(recordingId);
      if (currentRecordingId === recordingId) selectRecording(null);
      await reloadCurrentMeeting();
    } catch (e) {
      alert(t("sidebar.error.delete", { msg: (e as Error).message }));
    }
  }

  async function handleShare() {
    setMenuFor(null);
    alert(t("sidebar.share.notImplemented"));
  }

  function openMenu(e: React.MouseEvent, mid: string) {
    e.stopPropagation();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenuPos({ top: r.bottom + 4, left: r.left });
    setMenuFor(mid);
  }

  // Close menu on outside click
  useEffect(() => {
    if (!menuFor) return;
    function onClick(e: MouseEvent) {
      const t = e.target as Element;
      if (t.closest?.(".dropdown-menu") || t.closest?.(".row-menu-btn")) return;
      setMenuFor(null);
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [menuFor]);

  const menuMeeting = menuFor ? meetings.find((m) => m.id === menuFor) : null;

  return (
    <aside className="sidebar" id="sidebar">
      <button
        type="button"
        className="sidebar-brand"
        title={sidebarOpen ? "Về trang chọn project" : "Mở sidebar"}
        onClick={() => {
          if (!sidebarOpen) {
            toggleSidebar();
          } else {
            // Clear selection → TranscriptPane shows "no project" landing
            // where user can pick or create one.
            void selectMeeting(null);
          }
        }}
      >
        <span className="sidebar-brand-mark"></span>
        <span className="sidebar-brand-name">Mee</span>
      </button>
      <div className="sidebar-header">
        <button
          className="btn btn-primary btn-sm sidebar-new-btn"
          type="button"
          onClick={startNewProject}
          disabled={creatingProject}
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span className="sidebar-new-btn-label">{t("sidebar.newProject")}</span>
        </button>
      </div>

      <PromptDialog
        open={creatingProject}
        title={t("prompt.newProjectModalTitle")}
        placeholder={t("prompt.newProjectTitle")}
        onConfirm={commitNewProject}
        onCancel={() => setCreatingProject(false)}
      />

      <div className="sidebar-body">
        <div className="sidebar-section-label">{t("sidebar.recentProjects")}</div>
        <div className="sidebar-meetings">
          {meetingsLoading && <div className="sidebar-empty muted">{t("sidebar.loading")}</div>}
          {!meetingsLoading && meetings.length === 0 && (
            <div className="sidebar-empty muted">{t("sidebar.empty")}</div>
          )}
          {meetings.map((m) => {
            const isActive = currentMeetingId === m.id;
            const isExpanded = expanded.has(m.id) || isActive;
            const recordings =
              isActive && currentMeeting
                ? [...currentMeeting.recordings].sort((a, b) => {
                    const ta = a.started_at ? Date.parse(a.started_at) : 0;
                    const tb = b.started_at ? Date.parse(b.started_at) : 0;
                    return ta - tb;
                  })
                : [];
            return (
              <div
                key={m.id}
                className={`sidebar-meeting-group${isExpanded ? " expanded" : ""}`}
              >
                <div
                  className={`sidebar-meeting${isActive ? " active" : ""}`}
                  title={m.title}
                  onClick={() => {
                    if (renamingId === m.id) return;
                    selectMeeting(m.id);
                    setExpanded((s) => new Set(s).add(m.id));
                  }}
                >
                  {/* Initial letter shown ONLY in collapsed icon-rail
                   * mode (display:none by default; CSS toggles via
                   * body.sidebar-collapsed). Using SVG <text> with
                   * dominantBaseline="central" + textAnchor="middle"
                   * gives pixel-perfect optical centering regardless
                   * of font cap-height bias (DOM text centering drifts
                   * upward because line-box ≠ glyph box). */}
                  <svg
                    className="sidebar-meeting-initial"
                    viewBox="0 0 36 36"
                    width="36"
                    height="36"
                    aria-hidden="true"
                    focusable="false"
                  >
                    <text
                      x="18"
                      y="18"
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize="14"
                      fontWeight="700"
                      fill="currentColor"
                    >
                      {(m.title || "?").trim().charAt(0).toUpperCase() || "?"}
                    </text>
                  </svg>
                  <svg
                    className="sidebar-caret"
                    viewBox="0 0 12 12"
                    width="9"
                    height="9"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(m.id);
                    }}
                  >
                    <polyline points="4 2 8 6 4 10" />
                  </svg>
                  <div className="sidebar-meeting-content">
                    {renamingId === m.id ? (
                      <input
                        autoFocus
                        type="text"
                        className="field"
                        style={{ padding: "2px 6px", fontSize: 13, height: 24 }}
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        onBlur={commitRename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename();
                          if (e.key === "Escape") setRenamingId(null);
                        }}
                      />
                    ) : (
                      <>
                        <div
                          className="sidebar-meeting-title"
                          style={{ display: "flex", alignItems: "center", gap: 6 }}
                        >
                          {m.is_pinned && (
                            <svg
                              width="11"
                              height="11"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="var(--accent)"
                              strokeWidth="2.4"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              style={{ flexShrink: 0 }}
                              aria-label="Pinned"
                            >
                              <line x1="12" y1="17" x2="12" y2="22" />
                              <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24Z" />
                            </svg>
                          )}
                          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                            {m.title || t("sidebar.untitledProject")}
                          </span>
                        </div>
                        <div className="sidebar-meeting-meta">
                          {/* Project no longer has its own date (moved to
                              recordings in migration 0012). Show placeholder. */}
                          —
                          {m.has_summary && (
                            <span
                              className="sidebar-meeting-badge"
                              title={t("sidebar.hasProjectSummary")}
                              style={{ marginLeft: 6 }}
                            >
                              Σ
                            </span>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                  <button
                    className={`row-menu-btn${menuFor === m.id ? " open" : ""}`}
                    type="button"
                    aria-label="Project menu"
                    onClick={(e) => openMenu(e, m.id)}
                  >
                    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true">
                      <circle cx="8" cy="3" r="1.4" />
                      <circle cx="8" cy="8" r="1.4" />
                      <circle cx="8" cy="13" r="1.4" />
                    </svg>
                  </button>
                </div>

                {isExpanded && isActive && (
                  <div className="sidebar-recordings">
                    {recordings.length === 0 && (
                      <div className="muted" style={{ padding: "6px 12px", fontSize: 12 }}>
                        {t("sidebar.noRecordings")}
                      </div>
                    )}
                    {recordings.map((r, idx) => {
                      const recActive = currentRecordingId === r.id;
                      return (
                        <div
                          key={r.id}
                          className={`sidebar-recording${recActive ? " active" : ""}`}
                          onClick={() => selectRecording(r.id)}
                        >
                          <span className="rec-num">{idx + 1}</span>
                          <span className="rec-label">
                            {r.session_label || t("sidebar.recordingPlaceholder")}
                          </span>
                          <span className="rec-segs">{r.segment_count} {t("meta.seg")}</span>
                          <button
                            className="row-delete-btn"
                            type="button"
                            title={t("menu.delete")}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteRecording(r.id, r.session_label || "");
                            }}
                          >
                            ×
                          </button>
                        </div>
                      );
                    })}
                    <div
                      className="sidebar-add-rec"
                      onClick={() => handleNewRecording(m.id)}
                    >
                      {t("sidebar.addRecording")}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ─── Project context menu (portaled) ─── */}
      {menuMeeting &&
        createPortal(
          <div
            className="dropdown-menu open"
            style={{ top: menuPos.top, left: menuPos.left }}
          >
            <button className="dd-item" type="button" onClick={handleShare}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" />
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" /><line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
              </svg>
              <span>{t("menu.share")}</span>
            </button>
            <button className="dd-item" type="button" onClick={() => handlePin(menuMeeting)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="17" x2="12" y2="22" />
                <path d="M5 17h14l-2-7H7z" /><path d="M9 10V5l3-2 3 2v5" />
              </svg>
              <span>{menuMeeting.is_pinned ? t("menu.unpin") : t("menu.pin")}</span>
            </button>
            <button className="dd-item" type="button" onClick={() => startRename(menuMeeting)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z" />
              </svg>
              <span>{t("menu.rename")}</span>
            </button>
            <div className="dd-divider"></div>
            <button
              className="dd-item danger"
              type="button"
              onClick={() => handleDeleteProject(menuMeeting.id, menuMeeting.title)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <path d="M10 11v6M14 11v6" />
                <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
              <span>{t("menu.delete")}</span>
            </button>
          </div>,
          document.body,
        )}
      <div className="sidebar-footer">
        <UserControls user={user} />
      </div>
    </aside>
  );
}
