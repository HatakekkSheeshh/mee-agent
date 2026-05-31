// Custom TipTap mark for inline "tag" highlights (commitment / decision / blocker).
//
// Usage in editor:
//   editor.chain().focus().toggleMeetingTag("commitment").run()
//
// Rendered as <span class="meeting-tag tag-{type}">…</span> — styled in
// index.css so each tag type gets a distinct accent colour.
import { Mark, mergeAttributes } from "@tiptap/core";

export type TagType = "commitment" | "decision" | "blocker";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    meetingTag: {
      toggleMeetingTag: (type: TagType) => ReturnType;
    };
  }
}

export const MeetingTag = Mark.create({
  name: "meetingTag",

  addAttributes() {
    return {
      type: {
        default: "commitment",
        parseHTML: (el: HTMLElement) => el.getAttribute("data-tag"),
        renderHTML: (attrs: { type: TagType }) => ({
          "data-tag": attrs.type,
          class: `meeting-tag tag-${attrs.type}`,
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-tag]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes), 0];
  },

  addCommands() {
    return {
      toggleMeetingTag:
        (type: TagType) =>
        ({ commands, editor }) => {
          // If the current mark of this type is active, remove. Otherwise set
          // (replacing any other tag mark on the selection).
          const isActive =
            editor.isActive(this.name, { type });
          if (isActive) {
            return commands.unsetMark(this.name);
          }
          return commands.setMark(this.name, { type });
        },
    };
  },
});
