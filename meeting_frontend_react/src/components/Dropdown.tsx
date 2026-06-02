// Dropdown menu — matches old .dropdown-menu behavior: fixed-position, opens on
// trigger click, closes on outside click. Position computed from trigger rect.
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

interface Pos {
  top: number;
  left?: number;
  right?: number;
}

export function useDropdown(triggerRef: RefObject<HTMLElement>) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos>({ top: 0 });

  function place() {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const onRight = r.right > window.innerWidth - 300;
    setPos(onRight
      ? { top: r.bottom + 6, right: window.innerWidth - r.right }
      : { top: r.bottom + 6, left: r.left });
  }

  function toggle() {
    if (!open) place();
    setOpen((v) => !v);
  }
  function close() { setOpen(false); }

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      const menus = document.querySelectorAll(".dropdown-menu");
      for (const m of Array.from(menus)) if (m.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [open, triggerRef]);

  return { open, toggle, close, pos };
}

interface DropdownProps {
  open: boolean;
  pos: Pos;
  children: ReactNode;
}

export function Dropdown({ open, pos, children }: DropdownProps) {
  const style: React.CSSProperties = {
    top: pos.top,
    ...(pos.left !== undefined ? { left: pos.left } : {}),
    ...(pos.right !== undefined ? { right: pos.right } : {}),
  };
  return createPortal(
    <div className={`dropdown-menu${open ? " open" : ""}`} style={style}>
      {children}
    </div>,
    document.body,
  );
}

export type { Pos };
export const useRef_for_div = () => useRef<HTMLDivElement>(null);
