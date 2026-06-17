// Reusable confirm modal — matches legacy .mee-modal styling. Promise-based:
// caller does `const ok = await confirm({...})` instead of window.confirm().
import { useEffect } from "react";
import { createPortal } from "react-dom";
import { useApp } from "../store/AppContext";

export interface ConfirmOpts {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the primary button as danger (red) — for destructive ops. */
  danger?: boolean;
  /** Style the primary button with the brand accent (green). Ignored if danger. */
  accent?: boolean;
}

interface Props extends ConfirmOpts {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  danger,
  accent,
  onConfirm,
  onCancel,
}: Props) {
  const { t } = useApp();
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter") onConfirm();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onConfirm, onCancel]);

  if (!open) return null;

  return createPortal(
    <div className="mee-modal" aria-hidden={!open}>
      <div className="mee-modal-backdrop" onClick={onCancel}></div>
      <div className="mee-modal-card" role="dialog" aria-modal="true">
        {title && <div className="mee-modal-title">{title}</div>}
        <div className="mee-modal-body" style={{ fontSize: 14, lineHeight: 1.5 }}>
          {message}
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
            className={`btn btn-sm ${danger ? "btn-danger" : accent ? "btn-accent" : "btn-primary"}`}
            type="button"
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel || t("confirm.ok")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
