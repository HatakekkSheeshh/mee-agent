// CommentsPane — Notta-style threaded notes anchored to audio
// positions. Lives in the right slot (same place as ChatPane /
// InsightsPane), toggled by the comments button in the FloatingRail.
//
// Each comment can pin to a playback timestamp (audio.currentTime when
// the user typed it) so clicking the comment seeks the player back
// there. A textarea at the bottom adds new comments — Cmd/Ctrl+Enter
// submits.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../store/AppContext";
import { api, ApiError } from "../api/client";
import { ConfirmDialog } from "./ConfirmDialog";

interface Comment {
  id: string;
  recording_id: string;
  anchor_ms: number | null;
  segment_seq: number | null;
  text: string;
  created_at: string | null;
  edited_at: string | null;
  user: { id: string; display_name: string; email: string; avatar_url: string | null };
}

export function CommentsPane() {
  const { currentRecordingId, commentsOpen, toggleComments, t } = useApp();
  const [comments, setComments] = useState<Comment[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [posting, setPosting] = useState(false);
  const [anchorAtNow, setAnchorAtNow] = useState(true);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Comment | null>(null);
  const draftRef = useRef<HTMLTextAreaElement>(null);

  const reload = useCallback(async () => {
    if (!currentRecordingId) return;
    setLoading(true);
    try {
      const r = await api.recordings.listComments(currentRecordingId);
      setComments(r.comments);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [currentRecordingId]);

  useEffect(() => {
    if (commentsOpen) void reload();
  }, [commentsOpen, reload]);

  // Helper: read the audio player's current playback position. The
  // <audio> element is rendered by NottaCleanView (id="mom-result" lives
  // elsewhere). We grab the first <audio> we find on the page — there's
  // only one audio element per recording.
  function readAudioMs(): number | null {
    const el = document.querySelector("audio") as HTMLAudioElement | null;
    if (!el) return null;
    const t = el.currentTime;
    if (!isFinite(t) || t < 0) return null;
    return Math.round(t * 1000);
  }
  function seekAudioMs(ms: number) {
    const el = document.querySelector("audio") as HTMLAudioElement | null;
    if (!el) return;
    try { el.currentTime = ms / 1000; } catch { /* some browsers throw if not ready */ }
  }

  async function handlePost() {
    if (!currentRecordingId) return;
    const text = draft.trim();
    if (!text) return;
    setPosting(true);
    try {
      const created = await api.recordings.createComment(currentRecordingId, text, {
        anchor_ms: anchorAtNow ? readAudioMs() : null,
      });
      setComments((cur) => insertSorted(cur, created));
      setDraft("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : (err as Error).message;
      alert(t("comments.error.create", { msg }));
    } finally {
      setPosting(false);
    }
  }

  async function handleEditSave() {
    if (!editing) return;
    const text = editing.text.trim();
    if (!text) return;
    try {
      await api.recordings.editComment(editing.id, text);
      setComments((cur) =>
        cur.map((c) => (c.id === editing.id ? { ...c, text, edited_at: new Date().toISOString() } : c)),
      );
      setEditing(null);
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : (err as Error).message;
      alert(t("comments.error.edit", { msg }));
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    try {
      await api.recordings.removeComment(pendingDelete.id);
      setComments((cur) => cur.filter((c) => c.id !== pendingDelete.id));
      setPendingDelete(null);
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : (err as Error).message;
      alert(t("comments.error.delete", { msg }));
    }
  }

  // Group: comments anchored to audio vs general (anchor=null).
  const { anchored, general } = useMemo(() => {
    const a: Comment[] = [];
    const g: Comment[] = [];
    for (const c of comments) {
      if (c.anchor_ms != null) a.push(c);
      else g.push(c);
    }
    return { anchored: a, general: g };
  }, [comments]);

  if (!commentsOpen) return null;

  return (
    <aside className="pane pane-insights pane-comments">
      <div className="pane-header">
        <span className="pane-title">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}>
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          {t("comments.title")}
        </span>
        <div className="pane-meta">
          <span className="mono small" style={{ color: "var(--text-mute)" }}>
            {comments.length}{" "}
            {comments.length === 1 ? t("comments.count.one") : t("comments.count.many")}
          </span>
          <button
            className="icon-btn icon-btn-sm"
            type="button"
            title={t("comments.close")}
            onClick={toggleComments}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      <div className="pane-content comments-content">
        {!currentRecordingId ? (
          <div className="insights-empty">
            <div className="insights-empty-title">{t("comments.emptyNoRecording.title")}</div>
            <div className="insights-empty-sub muted small">{t("comments.emptyNoRecording.sub")}</div>
          </div>
        ) : (
          <>
            {loading && <div className="comments-loading muted small">{t("comments.loading")}</div>}
            {!loading && comments.length === 0 && (
              <div className="insights-empty">
                <div className="insights-empty-title">{t("comments.empty.title")}</div>
                <div className="insights-empty-sub muted small">{t("comments.empty.sub")}</div>
              </div>
            )}
            {anchored.length > 0 && (
              <div className="comments-section">
                <div className="comments-section-title">{t("comments.section.anchored")}</div>
                <ul className="comments-list">
                  {anchored.map((c) => (
                    <CommentRow
                      key={c.id}
                      c={c}
                      editing={editing}
                      setEditing={setEditing}
                      onSeek={() => c.anchor_ms != null && seekAudioMs(c.anchor_ms)}
                      onDelete={() => setPendingDelete(c)}
                      onSaveEdit={handleEditSave}
                    />
                  ))}
                </ul>
              </div>
            )}
            {general.length > 0 && (
              <div className="comments-section">
                <div className="comments-section-title">{t("comments.section.general")}</div>
                <ul className="comments-list">
                  {general.map((c) => (
                    <CommentRow
                      key={c.id}
                      c={c}
                      editing={editing}
                      setEditing={setEditing}
                      onSeek={null}
                      onDelete={() => setPendingDelete(c)}
                      onSaveEdit={handleEditSave}
                    />
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      {/* Composer at the bottom */}
      {currentRecordingId && (
        <div className="comments-composer">
          <label className="comments-anchor-toggle">
            <input
              type="checkbox"
              checked={anchorAtNow}
              onChange={(e) => setAnchorAtNow(e.target.checked)}
            />
            <span>{t("comments.anchorToggle")}</span>
          </label>
          <textarea
            ref={draftRef}
            className="comments-textarea"
            placeholder={t("comments.composerPlaceholder")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                void handlePost();
              }
            }}
            rows={3}
            disabled={posting}
          />
          <div className="comments-composer-row">
            <button
              className="btn btn-primary btn-sm"
              type="button"
              onClick={() => void handlePost()}
              disabled={posting || !draft.trim()}
            >
              {posting ? t("comments.posting") : t("comments.add")}
            </button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("comments.delete.title")}
        message={
          pendingDelete
            ? t("comments.delete.msg", { text: truncate(pendingDelete.text, 80) })
            : ""
        }
        confirmLabel={t("comments.delete.confirm")}
        cancelLabel={t("comments.delete.cancel")}
        danger
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => { void confirmDelete(); }}
      />
    </aside>
  );
}

interface RowProps {
  c: Comment;
  editing: { id: string; text: string } | null;
  setEditing: (v: { id: string; text: string } | null) => void;
  onSeek: (() => void) | null;
  onDelete: () => void;
  onSaveEdit: () => void;
}

function CommentRow({ c, editing, setEditing, onSeek, onDelete, onSaveEdit }: RowProps) {
  const { t } = useApp();
  const isEditing = editing?.id === c.id;
  const color = hashColor(c.user.display_name || c.user.email);
  const initial = (c.user.display_name || c.user.email).trim().charAt(0).toUpperCase();
  return (
    <li className="comments-row">
      <span className="comments-avatar" style={{ background: color }}>{initial}</span>
      <div className="comments-body">
        <div className="comments-meta">
          <span className="comments-author">{c.user.display_name}</span>
          {c.anchor_ms != null && onSeek && (
            <button
              type="button"
              className="comments-anchor-btn"
              onClick={onSeek}
              title={t("comments.seekTitle")}
            >
              ▸ {fmtMs(c.anchor_ms)}
            </button>
          )}
          <span className="comments-time muted small">
            {fmtCreated(c.created_at)}
            {c.edited_at && t("comments.edited")}
          </span>
        </div>
        {isEditing ? (
          <>
            <textarea
              className="comments-textarea"
              value={editing!.text}
              onChange={(e) => setEditing({ id: c.id, text: e.target.value })}
              autoFocus
              rows={3}
            />
            <div className="comments-row-actions">
              <button className="btn btn-ghost btn-xs" onClick={() => setEditing(null)}>{t("comments.cancel")}</button>
              <button className="btn btn-primary btn-xs" onClick={onSaveEdit}>{t("comments.save")}</button>
            </div>
          </>
        ) : (
          <>
            <div className="comments-text">{c.text}</div>
            <div className="comments-row-actions">
              <button
                className="btn btn-ghost btn-xs"
                onClick={() => setEditing({ id: c.id, text: c.text })}
              >
                {t("comments.edit")}
              </button>
              <button
                className="btn btn-ghost btn-xs danger"
                onClick={onDelete}
              >
                {t("comments.remove")}
              </button>
            </div>
          </>
        )}
      </div>
    </li>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────
function insertSorted(list: Comment[], c: Comment): Comment[] {
  const next = [...list, c];
  next.sort((a, b) => {
    const ax = a.anchor_ms ?? -1;
    const bx = b.anchor_ms ?? -1;
    if (ax !== bx) return ax - bx;
    return (a.created_at || "").localeCompare(b.created_at || "");
  });
  return next;
}

function fmtMs(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtCreated(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

const HUES = [142, 200, 280, 30, 350, 165, 50, 320];
function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return `hsl(${HUES[Math.abs(h) % HUES.length]}, 65%, 60%)`;
}
