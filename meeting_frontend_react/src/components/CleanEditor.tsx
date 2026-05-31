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
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { MeetingTag, type TagType } from "./MeetingTag";

interface Props {
  recordingId: string;
  segments: { speaker?: string; text: string; tags?: string[] }[];
  editedHtml?: string | null;
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

export function CleanEditor({ recordingId, segments, editedHtml }: Props) {
  const initialContent = (editedHtml && editedHtml.trim()) || segmentsToHtml(segments);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [lastSaved, setLastSaved] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);

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
    </div>
  );
}
