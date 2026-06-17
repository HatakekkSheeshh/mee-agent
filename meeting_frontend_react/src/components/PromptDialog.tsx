// PromptDialog — centered modal with a single text input. Drop-in
// replacement for window.prompt() that respects the app's dark theme.
//
// Usage:
//   <PromptDialog
//     open={creating}
//     title="Project mới"
//     placeholder="Tên project"
//     onConfirm={(value) => { create(value); setCreating(false); }}
//     onCancel={() => setCreating(false)}
//   />
//
// Enter = confirm (when input non-empty), Escape / backdrop = cancel.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../store/AppContext";

interface Props {
  open: boolean;
  title?: string;
  message?: string;
  placeholder?: string;
  initialValue?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function PromptDialog({
  open,
  title,
  message,
  placeholder,
  initialValue,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
}: Props) {
  const { t } = useApp();
  const [value, setValue] = useState(initialValue || "");

  // Reset input on every open so previous value doesn't leak between sessions.
  useEffect(() => {
    if (open) setValue(initialValue || "");
  }, [open, initialValue]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  function handleConfirm() {
    const v = value.trim();
    if (!v) return;
    onConfirm(v);
  }

  return createPortal(
    <div className="mee-modal" aria-hidden={!open}>
      <div className="mee-modal-backdrop" onClick={onCancel}></div>
      <div
        className="mee-modal-card"
        role="dialog"
        aria-modal="true"
        style={{ minWidth: 360 }}
      >
        {title && <div className="mee-modal-title">{title}</div>}
        {message && (
          <div
            className="mee-modal-body"
            style={{ fontSize: 13, lineHeight: 1.5, color: "var(--text-mute)" }}
          >
            {message}
          </div>
        )}
        <div style={{ padding: "12px 16px 4px" }}>
          <input
            autoFocus
            type="text"
            className="field"
            placeholder={placeholder}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleConfirm();
            }}
            style={{ width: "100%", height: 32, fontSize: 14, padding: "4px 10px" }}
          />
        </div>
        <div className="mee-modal-actions">
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={onCancel}
          >
            {cancelLabel || t("confirm.cancel")}
          </button>
          <button
            className="btn btn-primary btn-sm"
            type="button"
            onClick={handleConfirm}
            disabled={!value.trim()}
          >
            {confirmLabel || t("confirm.create")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
