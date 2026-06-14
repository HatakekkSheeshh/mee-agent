// MomEditor — rich-text editor for the per-recording MoM body.
//
// Wraps TipTap (already pulled in by CleanEditor) so the user gets
// undo/redo + bold/italic/underline/strike + bulleted & ordered lists
// + headings + size variants. Autosaves the HTML body to the new
// `mom_json.edited_html` field via `patchMomBody`; the view layer
// prefers that field when present so user revisions persist across
// regenerations (until the user explicitly re-runs Generate MoM).
//
// Edit/view toggle stays in the parent (MoMPane) — this component only
// owns the editor itself.

import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

interface Props {
  recordingId: string;
  /** HTML to seed the editor with on first mount. Caller picks
   * `mom_json.edited_html` if present, else renders MoMJson sections
   * to HTML as a starting point. */
  initialHtml: string;
  /** Called after a successful autosave so parent can update its
   * local mom_json cache without refetching. */
  onSaved?: (html: string) => void;
}

export function MomEditor({ recordingId, initialHtml, onSaved }: Props) {
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);

  const editor = useEditor({
    extensions: [StarterKit],
    content: initialHtml || "<p></p>",
    autofocus: false,
    onUpdate: ({ editor: ed }) => {
      // Debounced autosave — 800ms after the last keystroke. Cancels
      // any in-flight timer so rapid typing collapses to a single PATCH.
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        const html = ed.getHTML();
        const text = ed.getText();
        void (async () => {
          setStatus("saving");
          setErrMsg(null);
          try {
            await api.recordings.patchMomBody(recordingId, html, text);
            setStatus("saved");
            onSaved?.(html);
            window.setTimeout(() => setStatus("idle"), 1500);
          } catch (e) {
            const msg = e instanceof ApiError ? e.detail : (e as Error).message;
            setErrMsg(msg);
            setStatus("error");
          }
        })();
      }, 800);
    },
  });

  // Sync external initialHtml changes (e.g. parent reloads after
  // Generate MoM regenerated everything). Only force if the editor's
  // content actually differs to avoid wiping mid-typing.
  useEffect(() => {
    if (!editor) return;
    const current = editor.getHTML();
    if (initialHtml && initialHtml !== current) {
      editor.commands.setContent(initialHtml, { emitUpdate: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialHtml, editor]);

  // Clean up the pending timer on unmount to avoid a save after
  // the parent has switched recordings.
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    };
  }, []);

  if (!editor) return null;

  return (
    <div className="mom-editor">
      <div className="mom-editor-toolbar">
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
        <button
          className={`tt-btn${editor.isActive("code") ? " active" : ""}`}
          type="button"
          title="Inline code"
          onClick={() => editor.chain().focus().toggleCode().run()}
        >
          {"<>"}
        </button>
        <div className="tt-sep" />
        <button
          className={`tt-btn${editor.isActive("heading", { level: 1 }) ? " active" : ""}`}
          type="button"
          title="Heading 1"
          onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}
        >
          H1
        </button>
        <button
          className={`tt-btn${editor.isActive("heading", { level: 2 }) ? " active" : ""}`}
          type="button"
          title="Heading 2"
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          H2
        </button>
        <button
          className={`tt-btn${editor.isActive("heading", { level: 3 }) ? " active" : ""}`}
          type="button"
          title="Heading 3"
          onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        >
          H3
        </button>
        <div className="tt-sep" />
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
          className={`tt-btn${editor.isActive("blockquote") ? " active" : ""}`}
          type="button"
          title="Blockquote"
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
        >
          “”
        </button>
        <div className="tt-sep" />
        <span className="mom-editor-status">
          {status === "saving" && "Đang lưu…"}
          {status === "saved" && "✓ Đã lưu"}
          {status === "error" && (
            <span className="mom-editor-error" title={errMsg || ""}>
              ✗ Lưu lỗi
            </span>
          )}
        </span>
      </div>
      <EditorContent editor={editor} className="mom-editor-content" />
    </div>
  );
}
