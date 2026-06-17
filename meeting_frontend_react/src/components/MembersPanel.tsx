// Members + attendance manager — lives inside MeetingControl's Details
// panel. Two responsibilities:
//
//   1. Project membership: list members + invite by email + remove.
//   2. Per-recording attendance: when a recording is selected, show a
//      checkbox row per project member. Checked = present in THIS
//      session. Toggling rewrites the attendees string used by the
//      Details form's debounced save (which PATCHes recording.attendees).
//      Backend in turn uses len(attendees) as a min/max-speakers hint
//      to pyannote so diarization can't wildly over-cluster a short
//      monologue or under-cluster a busy session.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { MeetingMember } from "../types/api";
import { ConfirmDialog } from "./ConfirmDialog";
import { useApp } from "../store/AppContext";

type Role = "editor" | "viewer";

interface Props {
  meetingId: string;
  /** When set, render the per-recording attendance checkbox list and
   * wire toggles to `setAttendees`. When undefined (project overview),
   * only the project-members section renders. */
  recordingId?: string | null;
  /** Current free-text attendees string (comma-separated names). Owned
   * by MeetingControl so the existing debounced save still picks it up. */
  attendees: string;
  setAttendees: (s: string) => void;
}

export function MembersPanel({
  meetingId,
  recordingId,
  attendees,
  setAttendees,
}: Props) {
  const { t } = useApp();
  const [members, setMembers] = useState<MeetingMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("editor");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<
    Array<{ id: string; email: string; display_name: string }>
  >([]);
  // Pending "remove member" confirmation — null when no dialog open.
  const [pendingRemove, setPendingRemove] = useState<MeetingMember | null>(null);
  const [removing, setRemoving] = useState(false);
  // Role picker popover — Notta-style chip-button + dropdown panel that
  // matches the speaker chip dropdown look. Replaces the bare <select>
  // which couldn't be styled to match the rest of the UI.
  const [roleOpen, setRoleOpen] = useState(false);
  const roleWrapRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!roleOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!roleWrapRef.current) return;
      if (!roleWrapRef.current.contains(e.target as Node)) setRoleOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRoleOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [roleOpen]);

  const reloadMembers = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.meetings.listMembers(meetingId);
      setMembers(r.members);
    } catch {
      /* swallow — toast handled by parent flow */
    } finally {
      setLoading(false);
    }
  }, [meetingId]);

  useEffect(() => {
    void reloadMembers();
  }, [reloadMembers]);

  // Autocomplete on the invite input — debounce 250ms.
  useEffect(() => {
    const q = inviteEmail.trim();
    if (q.length < 1) {
      setSuggestions([]);
      return;
    }
    const tm = window.setTimeout(async () => {
      try {
        const r = await api.meetings.searchUsers(q, 6);
        // Exclude users already in members.
        const memberIds = new Set(members.map((m) => m.user_id));
        setSuggestions(r.users.filter((u) => !memberIds.has(u.id)));
      } catch {
        /* ignore */
      }
    }, 250);
    return () => window.clearTimeout(tm);
  }, [inviteEmail, members]);

  async function handleInvite(email: string) {
    const e = email.trim().toLowerCase();
    if (!e) return;
    setInviting(true);
    setInviteError(null);
    try {
      await api.meetings.addMember(meetingId, e, inviteRole);
      setInviteEmail("");
      setSuggestions([]);
      await reloadMembers();
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : (err as Error).message;
      setInviteError(msg);
    } finally {
      setInviting(false);
    }
  }

  async function confirmRemove() {
    if (!pendingRemove) return;
    setRemoving(true);
    try {
      await api.meetings.removeMember(meetingId, pendingRemove.user_id);
      await reloadMembers();
      setPendingRemove(null);
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : (err as Error).message;
      alert(t("members.error.remove", { msg }));
    } finally {
      setRemoving(false);
    }
  }

  // ─── Attendance helpers ────────────────────────────────────────────
  // attendees string ↔ Set<name> for easy toggling.
  const attendeeSet = new Set(
    attendees
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  );

  function toggleAttendee(name: string, checked: boolean) {
    const s = new Set(attendeeSet);
    if (checked) s.add(name);
    else s.delete(name);
    // Preserve order: keep names that survived in their original order,
    // then append any new ones from members (deduped).
    const ordered: string[] = [];
    const old = attendees.split(",").map((x) => x.trim()).filter(Boolean);
    for (const o of old) if (s.has(o)) {
      ordered.push(o);
      s.delete(o);
    }
    for (const remain of s) ordered.push(remain);
    setAttendees(ordered.join(", "));
  }

  // Skip synthetic attendee:<name> entries when rendering the
  // checkbox list — those are derived from this very attendees field
  // and would create a feedback loop. Only show real user members.
  const realMembers = members.filter((m) => !m.user_id.startsWith("attendee:"));

  return (
    <div className="members-panel">
      <div className="members-section">
        <div className="members-section-title">
          {t("members.section.project")}
          <span className="members-count">
            {realMembers.length} {t("members.count.people")}
          </span>
        </div>

        {loading && <div className="members-loading muted small">{t("members.loading")}</div>}

        {!loading && (
          <ul className="members-list">
            {realMembers.map((m) => (
              <li key={m.user_id} className="members-row">
                <span
                  className="members-avatar"
                  style={{ background: avatarColor(m.display_name) }}
                >
                  {initial(m.display_name)}
                </span>
                <div className="members-meta">
                  <div className="members-name">
                    {m.display_name}
                    {m.voice_enrolled && (
                      <span className="members-badge" title={t("members.voiceEnrolled")}>🎤</span>
                    )}
                  </div>
                  <div className="members-sub muted small">
                    {m.email}
                    {" · "}
                    <span className="members-role">{m.role}</span>
                  </div>
                </div>
                {m.role !== "owner" && (
                  <button
                    className="btn btn-ghost btn-xs"
                    type="button"
                    onClick={() => setPendingRemove(m)}
                    title={t("members.removeTitle")}
                  >
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="members-invite">
          <div className="members-invite-row">
            <input
              type="email"
              className="field"
              placeholder={t("members.invitePlaceholder")}
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleInvite(inviteEmail);
                }
              }}
              disabled={inviting}
            />
            <div className="members-role-wrap" ref={roleWrapRef}>
              <button
                type="button"
                className="members-role-btn"
                onClick={() => setRoleOpen((v) => !v)}
                disabled={inviting}
                aria-haspopup="listbox"
                aria-expanded={roleOpen}
                title={t("members.roleTitle")}
              >
                <span className="members-role-btn-label">
                  {inviteRole === "editor" ? t("members.role.editor") : t("members.role.viewer")}
                </span>
                <span className="members-role-btn-caret">▾</span>
              </button>
              {roleOpen && (
                <div className="members-role-popover" role="listbox">
                  {[
                    {
                      value: "editor" as Role,
                      label: t("members.role.editor"),
                      desc: t("members.role.editorDesc"),
                    },
                    {
                      value: "viewer" as Role,
                      label: t("members.role.viewer"),
                      desc: t("members.role.viewerDesc"),
                    },
                  ].map((opt) => {
                    const isCurrent = inviteRole === opt.value;
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        role="option"
                        aria-selected={isCurrent}
                        className={`members-role-option${
                          isCurrent ? " current" : ""
                        }`}
                        onClick={() => {
                          setInviteRole(opt.value);
                          setRoleOpen(false);
                        }}
                      >
                        <div className="members-role-option-main">
                          <span className="members-role-option-label">
                            {opt.label}
                          </span>
                          <span className="members-role-option-desc">
                            {opt.desc}
                          </span>
                        </div>
                        {isCurrent && (
                          <span className="members-role-option-check">✓</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <button
              className="btn btn-primary btn-sm"
              type="button"
              onClick={() => handleInvite(inviteEmail)}
              disabled={inviting || !inviteEmail.trim()}
            >
              {inviting ? t("members.inviting") : t("members.invite")}
            </button>
          </div>
          {suggestions.length > 0 && (
            <ul className="members-suggestions">
              {suggestions.map((u) => (
                <li key={u.id}>
                  <button
                    type="button"
                    className="members-suggestion-row"
                    onClick={() => void handleInvite(u.email)}
                  >
                    <span
                      className="members-avatar sm"
                      style={{ background: avatarColor(u.display_name) }}
                    >
                      {initial(u.display_name)}
                    </span>
                    <span>
                      <strong>{u.display_name}</strong>
                      <span className="muted small"> · {u.email}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {inviteError && (
            <div className="members-invite-error">{inviteError}</div>
          )}
        </div>
      </div>

      {recordingId && (
        <div className="members-section">
          <div className="members-section-title">
            {t("members.attendance.title")}
            <span className="members-count" title={t("members.attendance.countTitle")}>
              {attendeeSet.size} {t("members.count.people")}
            </span>
          </div>
          <div className="members-attendance-hint muted small">
            {t("members.attendance.hint")}
          </div>
          <ul className="members-list members-attendance">
            {realMembers.map((m) => {
              const checked = attendeeSet.has(m.display_name);
              return (
                <li key={m.user_id} className="members-attendance-row">
                  <label className="members-attendance-label">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => toggleAttendee(m.display_name, e.target.checked)}
                    />
                    <span
                      className="members-avatar sm"
                      style={{ background: avatarColor(m.display_name) }}
                    >
                      {initial(m.display_name)}
                    </span>
                    <span>{m.display_name}</span>
                  </label>
                </li>
              );
            })}
          </ul>
          {/* Names typed in the free-text field but not in members get
           * shown here so the user knows they're already counted. */}
          {Array.from(attendeeSet)
            .filter((n) => !realMembers.some((m) => m.display_name === n))
            .map((n) => (
              <div key={n} className="members-extra-attendee muted small">
                {t("members.attendance.guestAdded", { names: "" })}
                <strong>{n}</strong>
              </div>
            ))}
        </div>
      )}

      <ConfirmDialog
        open={pendingRemove !== null}
        title={t("members.confirm.delete.title")}
        message={
          pendingRemove
            ? t("members.confirm.delete.msg", {
                name: `${pendingRemove.display_name} (${pendingRemove.email})`,
              })
            : ""
        }
        confirmLabel={
          removing
            ? t("members.confirm.delete.deleting")
            : t("members.confirm.delete.confirm")
        }
        cancelLabel={t("members.confirm.delete.cancel")}
        danger
        onCancel={() => !removing && setPendingRemove(null)}
        onConfirm={() => { void confirmRemove(); }}
      />
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────
function initial(name: string): string {
  return (name || "?").trim().charAt(0).toUpperCase() || "?";
}

function avatarColor(name: string): string {
  // Deterministic hue from name hash so each user gets a stable color.
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 55% 55%)`;
}
