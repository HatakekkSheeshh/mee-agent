// VoiceprintsModal — Settings → "Voiceprints" → list + rename + delete.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "../api/client";
import type { Voiceprint } from "../types/api";
import { useApp } from "../store/AppContext";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function VoiceprintsModal({ open, onClose }: Props) {
  const { confirm, t } = useApp();
  const [rows, setRows] = useState<Voiceprint[]>([]);
  const [loading, setLoading] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    api.voiceprints
      .list()
      .then(setRows)
      .catch((e) => {
        const msg = e instanceof ApiError ? e.detail : (e as Error).message;
        alert(t("voiceprints.error.load", { msg }));
      })
      .finally(() => setLoading(false));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  async function commitRename(id: string) {
    const val = renameValue.trim();
    if (!val) {
      setRenamingId(null);
      return;
    }
    try {
      await api.voiceprints.rename(id, val);
      setRows((prev) => prev.map((r) => (r.id === id ? { ...r, name: val } : r)));
    } catch (e) {
      alert(t("voiceprints.error.rename", { msg: (e as Error).message }));
    } finally {
      setRenamingId(null);
    }
  }

  async function handleDelete(vp: Voiceprint) {
    const ok = await confirm({
      title: t("voiceprints.confirm.delete.title"),
      message: t("voiceprints.confirm.delete.msg", { name: vp.name }),
      confirmLabel: t("voiceprints.confirm.delete.confirm"),
      danger: true,
    });
    if (!ok) return;
    try {
      await api.voiceprints.remove(vp.id);
      setRows((prev) => prev.filter((r) => r.id !== vp.id));
    } catch (e) {
      alert(t("voiceprints.error.delete", { msg: (e as Error).message }));
    }
  }

  if (!open) return null;

  return createPortal(
    <div className="mee-modal" aria-hidden={!open}>
      <div className="mee-modal-backdrop" onClick={onClose}></div>
      <div className="mee-modal-card" role="dialog" aria-modal="true" style={{ maxWidth: 560 }}>
        <div className="mee-modal-title">🎤 Voiceprints</div>
        <div
          className="mee-modal-body"
          style={{ fontSize: 13, maxHeight: 360, overflowY: "auto" }}
        >
          <div className="muted small" style={{ marginBottom: 12 }}>
            Giọng nói đã được học từ các lần bạn label SPEAKER_NN. Meeting sau
            sẽ tự nhận diện những người này.
          </div>
          {loading && <div className="muted">{t("voiceprints.loading")}</div>}
          {!loading && rows.length === 0 && (
            <div className="muted" style={{ padding: 16, textAlign: "center" }}>
              <div>{t("voiceprints.empty.title")}</div>
              <div>{t("voiceprints.empty.body")}</div>
            </div>
          )}
          {rows.map((vp) => (
            <div
              key={vp.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 6px",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <div style={{ flex: 1 }}>
                {renamingId === vp.id ? (
                  <input
                    autoFocus
                    type="text"
                    className="field"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onBlur={() => commitRename(vp.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename(vp.id);
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    style={{ height: 28, padding: "2px 8px", fontSize: 13 }}
                  />
                ) : (
                  <>
                    <div style={{ fontWeight: 500 }}>{vp.name}</div>
                    <div className="muted small">
                      {t("voiceprints.samples", { n: vp.sample_count, date: (vp.last_seen_at || "").slice(0, 10) })}
                    </div>
                  </>
                )}
              </div>
              <button
                className="btn btn-ghost btn-xs"
                type="button"
                onClick={() => {
                  setRenamingId(vp.id);
                  setRenameValue(vp.name);
                }}
                disabled={renamingId === vp.id}
              >
                {t("voiceprints.rename")}
              </button>
              <button
                className="btn btn-ghost btn-xs"
                type="button"
                onClick={() => handleDelete(vp)}
                style={{ color: "var(--danger)" }}
              >
                {t("voiceprints.delete")}
              </button>
            </div>
          ))}
        </div>
        <div className="mee-modal-actions">
          <button className="btn btn-primary btn-sm" type="button" onClick={onClose}>
            {t("voiceprints.close")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
