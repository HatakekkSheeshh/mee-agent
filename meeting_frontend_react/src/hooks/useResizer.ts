// Drag-to-resize hook. Returns an onMouseDown handler.
//
// Captures `startX` AND `startValue` at the mousedown moment — does NOT track
// state updates during the drag. This is the key to smooth dragging: width is
// always computed as `startValue + (currentMouseX - startX)`, so the cursor
// position maps 1:1 to the pane width.
//
// Prior buggy version updated startValue on every state change → delta got
// compounded each tick, making the pane "jump" far beyond the cursor.
import { useCallback } from "react";

interface Opts {
  /** Read the current width when drag starts. Called once per mousedown. */
  getStartValue: () => number;
  /** Called with the new computed width on every mousemove. */
  onChange: (next: number) => void;
  /** Hard minimum width in px. */
  min: number;
  /** Hard maximum — number or function (eg. depends on window size). */
  max: number | (() => number);
  /** If true, dragging LEFT increases the value (use for right-side handles). */
  invert?: boolean;
  /** If set, persist final value to localStorage under this key on mouseup. */
  storageKey?: string;
}

export function useResizer({
  getStartValue,
  onChange,
  min,
  max,
  invert = false,
  storageKey,
}: Opts) {
  return useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startV = getStartValue();
      const getMax = typeof max === "function" ? max : () => max;
      const handle = e.currentTarget as HTMLElement;

      document.body.classList.add("resizing");
      handle.classList.add("dragging");
      let lastV = startV;

      const onMove = (ev: MouseEvent) => {
        const raw = ev.clientX - startX;
        const delta = invert ? -raw : raw;
        const next = Math.max(min, Math.min(getMax(), startV + delta));
        lastV = next;
        onChange(next);
      };
      const onUp = () => {
        document.body.classList.remove("resizing");
        handle.classList.remove("dragging");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        if (storageKey) localStorage.setItem(storageKey, String(lastV));
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [getStartValue, onChange, min, max, invert, storageKey],
  );
}
