// CleanEditor — TipTap WYSIWYG editor for the Clean transcript view.
//
// Phase 1: basic StarterKit with manual save (Ctrl+S).
// Phase 2 (this file):
//   - Debounced auto-save (1.5s after last edit)
//   - Undo / Redo buttons (StarterKit ships history; just wire commands)
//   - Custom MeetingTag mark — toggle commitment / decision / blocker on selection
//   - Save-state UI: ● dirty / ⟳ saving / ✓ saved HH:MM:SS
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "../api/client";
import { MeetingTag, type TagType } from "./MeetingTag";
import { useApp } from "../store/AppContext";

interface Props {
  recordingId: string;
  segments: { speaker?: string; text: string; tags?: string[] }[];
  editedHtml?: string | null;
  /** Known speakers (cluster_mapping values) for the "reassign speaker"
   * dropdown that opens when user clicks on a speaker prefix. */
  clusterMapping?: Record<string, string>;
  /** Called after every successful save with the latest HTML. Lets the parent
   * keep its editedHtml state fresh so tab-switching (Raw ↔ Clean) doesn't
   * remount the editor with stale content. */
  onSaved?: (html: string, text: string) => void;
}

function segmentsToHtml(segs: { speaker?: string; text: string }[]): string {
  return segs
    .map((s) => {
      const spk = s.speaker ? `<strong>${escapeHtml(s.speaker)}:</strong> ` : "";
      return `<p>${spk}${escapeHtml(s.text || "")}</p>`;
    })
    .join("");
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

export function CleanEditor({ recordingId, segments, editedHtml, clusterMapping, onSaved }: Props) {
  const { t } = useApp();
  const initialContent = (editedHtml && editedHtml.trim()) || segmentsToHtml(segments);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);

  // Speaker reassign dropdown — opens when user clicks a "<strong>Name:</strong>"
  // prefix at the start of a paragraph.
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerPos, setPickerPos] = useState({ top: 0, left: 0 });
  const [pickerTarget, setPickerTarget] = useState<HTMLElement | null>(null);
  // Inline "+ Tên khác" input inside the dropdown — replaces window.prompt
  // so user stays in the same visual context instead of getting a native modal.
  const [customNameOpen, setCustomNameOpen] = useState(false);
  const [customNameValue, setCustomNameValue] = useState("");

  // Speakers found in current editor DOM (recomputed each time picker opens).
  // This is the authoritative list because user-added speakers (via "+ Tên
  // khác…" or previous reassigns) live only in editor state, not in props.
  const [livePickerSpeakers, setLivePickerSpeakers] = useState<string[]>([]);

  const knownSpeakers = useMemo(() => {
    const set = new Set<string>(livePickerSpeakers);
    for (const seg of segments) {
      // Include raw cluster ids like "SPEAKER_00" so user can reassign one
      // Unknown block to a different cluster (the cleaner now keeps cluster
      // ids in segments when name can't be inferred).
      if (seg.speaker && seg.speaker !== "Unknown") set.add(seg.speaker);
    }
    for (const [cid, name] of Object.entries(clusterMapping || {})) {
      if (name && name !== "Unknown") set.add(name);
      else if (cid) set.add(cid); // expose raw cluster id when no name yet
    }
    return [...set];
  }, [segments, clusterMapping, livePickerSpeakers]);

  const editor = useEditor({
    extensions: [StarterKit, MeetingTag],
    content: initialContent,
    editorProps: {
      attributes: { class: "tiptap-content", spellcheck: "false" },
    },
    onUpdate: () => {
      setSaveState("dirty");
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => doSave(), 1500);
    },
  });

  async function doSave() {
    if (!editor) return;
    const html = editor.getHTML();
    const text = editor.getText();
    if (!text.trim()) {
      setSaveState("idle");
      return;
    }
    setSaveState("saving");
    setErrorMsg(null);
    try {
      await api.recordings.saveCleanEdited(recordingId, html, text);
      setSaveState("saved");
      setLastSaved(new Date().toLocaleTimeString());
      // Notify parent so it keeps its editedHtml in sync — prevents stale
      // content from showing up when user toggles Raw → Clean tabs.
      onSaved?.(html, text);
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      setErrorMsg(msg);
      setSaveState("error");
    }
  }

  // Re-init on recording switch
  useEffect(() => {
    if (!editor) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    editor.commands.setContent(initialContent, { emitUpdate: false });
    setSaveState("idle");
    setLastSaved(null);
    setErrorMsg(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingId, editor]);

  // Cleanup pending timer on unmount
  useEffect(() => () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); }, []);

  // Click on a speaker prefix → open reassign dropdown.
  // Detects the first <strong> of a paragraph whose text ends with ":".
  useEffect(() => {
    if (!editor) return;
    function onClick(e: MouseEvent) {
      const target = e.target as HTMLElement;
      const strong = target.closest("strong");
      if (!strong) return;
      const text = (strong.textContent || "").trim();
      if (!text.endsWith(":")) return;
      const para = strong.closest("p");
      if (!para || para.firstChild !== strong) return;
      e.preventDefault();
      e.stopPropagation();
      // Scan the entire editor for speaker prefixes — this includes names
      // the user typed via "+ Tên khác…" earlier, which aren't in the
      // `segments` prop (segments comes from server-side /clean response,
      // not from current editor state).
      const root = editor.view.dom as HTMLElement;
      const live = new Set<string>();
      const clickedName = text.replace(/:$/, "").trim();
      root.querySelectorAll("p > strong:first-child").forEach((el) => {
        const t = (el.textContent || "").trim();
        if (t.endsWith(":")) {
          const n = t.slice(0, -1).trim();
          if (n && n !== "Unknown" && n !== clickedName) live.add(n);
        }
      });
      setLivePickerSpeakers([...live]);
      const rect = strong.getBoundingClientRect();
      setPickerPos({ top: rect.bottom + 4, left: rect.left });
      setPickerTarget(strong);
      setPickerOpen(true);
      // Reset "+ Tên khác" inline input — picker should always open with the
      // list view, never with the input pre-expanded from a previous open.
      setCustomNameOpen(false);
      setCustomNameValue("");
    }
    const root = editor.view.dom;
    root.addEventListener("click", onClick);
    return () => root.removeEventListener("click", onClick);
  }, [editor]);

  // Close picker on outside click / Escape
  useEffect(() => {
    if (!pickerOpen) return;
    function onDocClick(e: MouseEvent) {
      const t = e.target as Element;
      if (t.closest?.(".speaker-picker")) return;
      setPickerOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPickerOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [pickerOpen]);

  function reassignTo(newName: string) {
    if (!editor || !pickerTarget) {
      setPickerOpen(false);
      return;
    }
    try {
      // Compute ProseMirror positions of the <strong> node, then replace its
      // text content with "{newName}:" (preserves bold formatting).
      const view = editor.view;
      const from = view.posAtDOM(pickerTarget, 0);
      const innerText = pickerTarget.textContent || "";
      const to = from + innerText.length;
      if (from < 0 || to <= from) return;
      editor
        .chain()
        .focus()
        .insertContentAt({ from, to }, `${newName}:`, { updateSelection: false })
        .run();
    } catch (e) {
      console.warn("Reassign failed:", e);
    } finally {
      setPickerOpen(false);
      setPickerTarget(null);
    }
  }

  // Ctrl+S / Cmd+S — force immediate save
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        doSave();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor]);

  if (!editor) return <div className="muted">Đang load editor…</div>;

  function applyTag(type: TagType) {
    editor!.chain().focus().toggleMeetingTag(type).run();
  }

  const stateBadge = () => {
    switch (saveState) {
      case "dirty":
        return <span className="muted small" style={{ marginRight: 8 }}>● Đang gõ…</span>;
      case "saving":
        return <span className="muted small" style={{ marginRight: 8 }}>⟳ Đang lưu…</span>;
      case "saved":
        return <span className="small" style={{ marginRight: 8, color: "var(--accent)" }}>✓ Đã lưu {lastSaved}</span>;
      case "error":
        return <span className="small" style={{ marginRight: 8, color: "var(--danger)" }} title={errorMsg || ""}>⚠ Lưu lỗi</span>;
      default:
        return <span className="muted small" style={{ marginRight: 8 }}>{lastSaved ? `Lưu lần cuối ${lastSaved}` : ""}</span>;
    }
  };

  return (
    <div className="clean-editor">
      <div className="clean-editor-toolbar">
        {/* History */}
        <button
          className="tt-btn"
          type="button"
          title="Undo (Ctrl+Z)"
          onClick={() => editor.chain().focus().undo().run()}
          disabled={!editor.can().undo()}
        >
          ↶
        </button>
        <button
          className="tt-btn"
          type="button"
          title="Redo (Ctrl+Y)"
          onClick={() => editor.chain().focus().redo().run()}
          disabled={!editor.can().redo()}
        >
          ↷
        </button>
        <div className="tt-sep" />

        {/* Formatting */}
        <button
          className={`tt-btn${editor.isActive("bold") ? " active" : ""}`}
          type="button"
          title="Bold (Ctrl+B)"
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          <b>B</b>
        </button>
        <button
          className={`tt-btn${editor.isActive("italic") ? " active" : ""}`}
          type="button"
          title="Italic (Ctrl+I)"
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          <i>I</i>
        </button>
        <button
          className={`tt-btn${editor.isActive("strike") ? " active" : ""}`}
          type="button"
          title="Strikethrough"
          onClick={() => editor.chain().focus().toggleStrike().run()}
        >
          <s>S</s>
        </button>
        <div className="tt-sep" />

        {/* Lists / heading */}
        <button
          className={`tt-btn${editor.isActive("bulletList") ? " active" : ""}`}
          type="button"
          title="Bullet list"
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        >
          •
        </button>
        <button
          className={`tt-btn${editor.isActive("orderedList") ? " active" : ""}`}
          type="button"
          title="Ordered list"
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
        >
          1.
        </button>
        <button
          className={`tt-btn${editor.isActive("heading", { level: 2 }) ? " active" : ""}`}
          type="button"
          title="Heading 2"
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          H2
        </button>
        <div className="tt-sep" />

        {/* Meeting tags */}
        <button
          className={`tt-btn tt-tag tt-tag-commitment${editor.isActive("meetingTag", { type: "commitment" }) ? " active" : ""}`}
          type="button"
          title="Đánh dấu commitment (chọn text trước)"
          onClick={() => applyTag("commitment")}
        >
          Commit
        </button>
        <button
          className={`tt-btn tt-tag tt-tag-decision${editor.isActive("meetingTag", { type: "decision" }) ? " active" : ""}`}
          type="button"
          title="Đánh dấu decision"
          onClick={() => applyTag("decision")}
        >
          Decision
        </button>
        <button
          className={`tt-btn tt-tag tt-tag-blocker${editor.isActive("meetingTag", { type: "blocker" }) ? " active" : ""}`}
          type="button"
          title="Đánh dấu blocker"
          onClick={() => applyTag("blocker")}
        >
          Blocker
        </button>

        <div className="spacer" />

        {stateBadge()}
      </div>
      <EditorContent editor={editor} className="clean-editor-content" />

      {/* Speaker reassign dropdown (portal) */}
      {pickerOpen && createPortal(
        <div
          className="dropdown-menu open speaker-picker"
          style={{ top: pickerPos.top, left: pickerPos.left, minWidth: 200 }}
          // Stop BOTH mousedown + click bubble to document. The outside-click
          // handler uses click; when a button inside is clicked, it re-renders
          // and the button leaves the DOM → e.target.closest('.speaker-picker')
          // returns null → handler treats it as outside-click and closes.
          // Stop here so the document handler never even sees it.
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="dd-label">{t("speakerPicker.title")}</div>
          {knownSpeakers.length === 0 && !customNameOpen && (
            <div className="muted small" style={{ padding: "8px 10px" }}>
              {t("speakerPicker.empty")}
            </div>
          )}
          {knownSpeakers.map((name) => (
            <button
              key={name}
              className="dd-item"
              type="button"
              onClick={() => reassignTo(name)}
            >
              <span style={{ width: 16 }}></span>
              <span>{name}</span>
            </button>
          ))}
          <div className="dd-divider"></div>
          {customNameOpen ? (
            <div style={{ padding: "6px 8px" }}>
              <input
                autoFocus
                type="text"
                className="field"
                placeholder={t("speakerPicker.customPlaceholder")}
                value={customNameValue}
                onChange={(e) => setCustomNameValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const v = customNameValue.trim();
                    setCustomNameOpen(false);
                    setCustomNameValue("");
                    if (v) reassignTo(v);
                    else setPickerOpen(false);
                  }
                  if (e.key === "Escape") {
                    setCustomNameOpen(false);
                    setCustomNameValue("");
                  }
                }}
                style={{ width: "100%", height: 28, padding: "2px 8px", fontSize: 13 }}
              />
            </div>
          ) : (
            <button
              className="dd-item"
              type="button"
              onClick={() => {
                setCustomNameOpen(true);
                setCustomNameValue("");
              }}
            >
              <span style={{ width: 16 }}></span>
              <span className="muted">{t("speakerPicker.addCustom")}</span>
            </button>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
