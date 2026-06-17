import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  type CompositionEvent,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { pmCommandRange } from "../utils/pmAgent";

/** Imperative handle exposed to the parent (used by the slash-command accept). */
export interface ChatInputHandle {
  focus: () => void;
}

interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  /** Forwarded to the parent for slash-menu navigation + Enter-to-send. */
  onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void;
  disabled?: boolean;
  placeholder?: string;
  ariaLabel?: string;
}

/**
 * The CSS Custom Highlight API registry is shared per-document; one fixed name
 * is enough since a single chat input exists at a time. Feature-detected so the
 * editor degrades to a plain (un-highlighted) box on older browsers — the text
 * and send behaviour are unaffected.
 */
const HL_NAME = "pm-cmd";

interface HighlightCtor {
  new (...ranges: Range[]): object;
}
interface HighlightRegistryLike {
  set: (name: string, highlight: object) => void;
  delete: (name: string) => void;
}

const HighlightImpl: HighlightCtor | undefined = (
  window as unknown as { Highlight?: HighlightCtor }
).Highlight;
const highlightRegistry: HighlightRegistryLike | undefined = (
  CSS as unknown as { highlights?: HighlightRegistryLike }
).highlights;
const HL_SUPPORTED = !!HighlightImpl && !!highlightRegistry;

/** Map a [start,end) character span onto a DOM Range across `root`'s text nodes. */
function rangeFromOffsets(root: Node, start: number, end: number): Range | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let pos = 0;
  let startNode: Node | null = null;
  let startOffset = 0;
  let endNode: Node | null = null;
  let endOffset = 0;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const len = n.textContent?.length ?? 0;
    if (startNode === null && pos + len >= start) {
      startNode = n;
      startOffset = start - pos;
    }
    if (pos + len >= end) {
      endNode = n;
      endOffset = end - pos;
      break;
    }
    pos += len;
  }
  if (!startNode || !endNode) return null;
  const range = document.createRange();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset);
  return range;
}

function setCaretToEnd(el: HTMLElement): void {
  const sel = window.getSelection();
  if (!sel) return;
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
}

/**
 * Plain-text chat editor backed by a `contenteditable` div. The `/pm-agent`
 * command prefix is highlighted in place via the CSS Custom Highlight API,
 * which colours a text Range WITHOUT inserting any node — so the caret and
 * Vietnamese IME composition are never disturbed (the whole reason we don't
 * wrap the prefix in a styled span).
 *
 * The DOM text is uncontrolled: it is read on input and emitted via `onChange`,
 * and only written back when `value` changes programmatically (slash accept,
 * send-clear) — never on every render, so React never wipes the live text.
 */
export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(
  function ChatInput({ value, onChange, onKeyDown, disabled, placeholder, ariaLabel }, ref) {
    const elRef = useRef<HTMLDivElement>(null);

    useImperativeHandle(ref, () => ({ focus: () => elRef.current?.focus() }), []);

    const applyHighlight = useCallback((el: HTMLElement) => {
      if (!HL_SUPPORTED) return;
      const span = pmCommandRange(el.textContent ?? "");
      const range = span && rangeFromOffsets(el, span.start, span.end);
      if (range && HighlightImpl && highlightRegistry) {
        highlightRegistry.set(HL_NAME, new HighlightImpl(range));
      } else {
        highlightRegistry?.delete(HL_NAME);
      }
    }, []);

    // Push `value` into the DOM only on programmatic changes. User edits arrive
    // via onChange, which sets `value` to exactly the DOM text, so this is a
    // no-op for typing and fires only for slash-accept / send-clear.
    useEffect(() => {
      const el = elRef.current;
      if (!el) return;
      if (value !== (el.textContent ?? "")) {
        el.textContent = value;
        if (document.activeElement === el) setCaretToEnd(el);
      }
      applyHighlight(el);
    }, [value, applyHighlight]);

    // Drop the highlight when this editor unmounts (shared document registry).
    useEffect(() => () => highlightRegistry?.delete(HL_NAME), []);

    const emit = useCallback(
      (el: HTMLElement) => {
        const text = el.textContent ?? "";
        // Browsers leave a bogus <br> after the user deletes all text; clear it
        // so the :empty placeholder shows and textContent stays clean.
        if (text === "" && el.innerHTML !== "") el.innerHTML = "";
        onChange(text);
        applyHighlight(el);
      },
      [onChange, applyHighlight],
    );

    const handleInput = useCallback(
      (e: FormEvent<HTMLDivElement>) => emit(e.currentTarget),
      [emit],
    );

    const handleCompositionEnd = useCallback(
      (e: CompositionEvent<HTMLDivElement>) => emit(e.currentTarget),
      [emit],
    );

    // Insert a literal "\n" (rendered via white-space: pre-wrap) instead of the
    // default block/<br> contenteditable would create — keeps textContent clean.
    const insertNewline = useCallback((el: HTMLElement) => {
      const sel = window.getSelection();
      if (!sel || !sel.rangeCount) return;
      const range = sel.getRangeAt(0);
      range.deleteContents();
      const node = document.createTextNode("\n");
      range.insertNode(node);
      range.setStartAfter(node);
      range.collapse(true);
      sel.removeAllRanges();
      sel.addRange(range);
      emit(el);
    }, [emit]);

    const handleKeyDown = useCallback(
      (e: KeyboardEvent<HTMLDivElement>) => {
        onKeyDown?.(e); // parent: slash-menu nav + Enter-to-send (may preventDefault)
        if (e.defaultPrevented) return;
        // Any Enter the parent did not consume is a newline request (Shift+Enter).
        if (e.key === "Enter") {
          e.preventDefault();
          insertNewline(e.currentTarget);
        }
      },
      [onKeyDown, insertNewline],
    );

    // Force plain-text paste so no foreign HTML/spans enter the editor.
    const handlePaste = useCallback(
      (e: ClipboardEvent<HTMLDivElement>) => {
        e.preventDefault();
        const text = e.clipboardData.getData("text/plain");
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        const range = sel.getRangeAt(0);
        range.deleteContents();
        const node = document.createTextNode(text);
        range.insertNode(node);
        range.setStartAfter(node);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        emit(e.currentTarget);
      },
      [emit],
    );

    return (
      <div
        ref={elRef}
        className="chat-input chat-input-ce"
        role="textbox"
        aria-multiline="true"
        aria-label={ariaLabel}
        aria-disabled={disabled || undefined}
        data-placeholder={placeholder}
        contentEditable={!disabled}
        suppressContentEditableWarning
        spellCheck={false}
        onInput={handleInput}
        onCompositionEnd={handleCompositionEnd}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />
    );
  },
);
