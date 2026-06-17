// Dropdown menu — matches old .dropdown-menu behavior: fixed-position, opens on
// trigger click, closes on outside click. Position computed from trigger rect.
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

interface Pos {
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
}

export function useDropdown(
  triggerRef: RefObject<HTMLElement>,
  opts: { placement?: "top" | "bottom" } = {},
) {
  const placement = opts.placement || "bottom";
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos>({ top: 0 });

  function place() {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const onRight = r.right > window.innerWidth - 300;
    const horizontal = onRight
      ? { right: window.innerWidth - r.right }
      : { left: r.left };
    // Vertical: above the trigger when placement="top" (menu's
    // bottom anchored above the trigger's top); below otherwise.
    const vertical = placement === "top"
      ? { bottom: window.innerHeight - r.top + 6 }
      : { top: r.bottom + 6 };
    setPos({ ...vertical, ...horizontal });
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
    ...(pos.top !== undefined ? { top: pos.top } : {}),
    ...(pos.bottom !== undefined ? { bottom: pos.bottom } : {}),
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
