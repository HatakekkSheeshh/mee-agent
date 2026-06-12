import { useState } from "react";
import { useApp } from "../store/AppContext";

/** One row of the reconcile template the agent built from the meeting's MoM.
 * Mirrors the backend `build_task_items` shape ({subject, assignee, due_date,
 * description}). */
export interface TaskTemplateItem {
  subject: string;
  assignee: string;
  due_date: string;
  description: string;
}

/** The `create_task` GATE-1 template: a target Redmine project (default = the
 * meeting title, editable here) + the flat task items to reconcile. */
export interface TaskTemplate {
  project: string;
  items: TaskTemplateItem[];
}

function _str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Parse a pending-action `args` blob into a TaskTemplate, or null if it isn't
 * a create_task template (so the caller can fall back to the generic card).
 * Tolerant of missing fields — the agent only guarantees `items[].subject`. */
export function parseTaskTemplate(
  args: Record<string, unknown> | null | undefined,
): TaskTemplate | null {
  if (!args || typeof args !== "object") return null;
  const rawItems = (args as { items?: unknown }).items;
  if (!Array.isArray(rawItems)) return null;
  const items: TaskTemplateItem[] = rawItems.map((it) => {
    const o = (it ?? {}) as Record<string, unknown>;
    return {
      subject: _str(o.subject),
      assignee: _str(o.assignee),
      due_date: _str(o.due_date),
      description: _str(o.description),
    };
  });
  return { project: _str((args as { project?: unknown }).project), items };
}

// ─── Grouped-by-person view model ──────────────────────────────────
// The card displays/edits the template grouped by assignee (one block per
// person), then flattens back to the flat {project, items} shape on approve.

interface GroupItem {
  subject: string;
  due_date: string;
  description: string;
}
interface TaskGroup {
  name: string; // assignee
  items: GroupItem[];
}

function groupByAssignee(items: TaskTemplateItem[]): TaskGroup[] {
  const order: string[] = [];
  const byName = new Map<string, TaskGroup>();
  for (const it of items) {
    const name = it.assignee ?? "";
    let g = byName.get(name);
    if (!g) {
      g = { name, items: [] };
      byName.set(name, g);
      order.push(name);
    }
    g.items.push({ subject: it.subject, due_date: it.due_date, description: it.description });
  }
  return order.map((n) => byName.get(n)!);
}

function flattenGroups(groups: TaskGroup[]): TaskTemplateItem[] {
  return groups.flatMap((g) =>
    g.items
      .filter((it) => it.subject.trim() !== "")
      .map((it) => ({
        subject: it.subject.trim(),
        assignee: g.name.trim(),
        due_date: it.due_date,
        description: it.description,
      })),
  );
}

interface CreateTaskCardProps {
  template: TaskTemplate;
  busy: boolean;
  /** Approve GATE 1 with the (possibly edited) template — sent as `edited_args`,
   * which the backend merges into the reconcile payload before the pm handoff.
   * `reason` is an optional approval note persisted on the pending action. */
  onApprove: (edited: TaskTemplate, reason: string) => void;
  onReject: () => void;
}

export function CreateTaskCard({ template, busy, onApprove, onReject }: CreateTaskCardProps) {
  const { t } = useApp();
  const [project, setProject] = useState(template.project);
  const [groups, setGroups] = useState<TaskGroup[]>(() => groupByAssignee(template.items));
  const [reason, setReason] = useState("");

  const setGroupName = (gi: number, name: string) =>
    setGroups((prev) => prev.map((g, i) => (i === gi ? { ...g, name } : g)));

  const updateItem = (gi: number, ii: number, field: keyof GroupItem, value: string) =>
    setGroups((prev) =>
      prev.map((g, i) =>
        i === gi
          ? { ...g, items: g.items.map((it, j) => (j === ii ? { ...it, [field]: value } : it)) }
          : g,
      ),
    );

  const removeItem = (gi: number, ii: number) =>
    setGroups((prev) =>
      prev
        .map((g, i) => (i === gi ? { ...g, items: g.items.filter((_, j) => j !== ii) } : g))
        .filter((g) => g.items.length > 0),
    );

  const blankItem = (): GroupItem => ({ subject: "", due_date: "", description: "" });

  const addItem = (gi: number) =>
    setGroups((prev) =>
      prev.map((g, i) => (i === gi ? { ...g, items: [...g.items, blankItem()] } : g)),
    );

  const addGroup = () =>
    setGroups((prev) => [...prev, { name: "", items: [blankItem()] }]);

  const totalItems = groups.reduce((n, g) => n + g.items.length, 0);
  const canApprove =
    !busy &&
    project.trim() !== "" &&
    totalItems > 0 &&
    groups.every((g) => g.items.every((it) => it.subject.trim() !== ""));

  return (
    <div className="msg msg-agent pending-action">
      <div className="pending-title">{t("chat.task.title")}</div>

      <label className="task-field task-project-field">
        <span className="task-label">{t("chat.task.project")}</span>
        <input
          className="chat-input task-project"
          type="text"
          value={project}
          disabled={busy}
          placeholder={t("chat.task.project")}
          onChange={(e) => setProject(e.target.value)}
        />
      </label>

      {totalItems === 0 && (
        <div className="task-empty small">{t("chat.task.empty")}</div>
      )}
      <div className="task-groups">
          {groups.map((g, gi) => (
            <div key={gi} className="task-group">
              <label className="task-field">
                <span className="task-label">{t("chat.task.assignee")}</span>
                <input
                  className="chat-input task-group-name"
                  type="text"
                  value={g.name}
                  disabled={busy}
                  placeholder={t("chat.task.unassigned")}
                  onChange={(e) => setGroupName(gi, e.target.value)}
                />
              </label>
              <ul className="task-items">
                {g.items.map((it, ii) => (
                  <li key={ii} className="task-item">
                    <div className="task-item-body">
                      <label className="task-mini">
                        <span className="task-mini-label">{t("chat.task.subject")}</span>
                        <input
                          className="chat-input task-subject"
                          type="text"
                          value={it.subject}
                          disabled={busy}
                          placeholder={t("chat.task.subjectPlaceholder")}
                          onChange={(e) => updateItem(gi, ii, "subject", e.target.value)}
                        />
                      </label>
                      <label className="task-mini">
                        <span className="task-mini-label">{t("chat.task.due")}</span>
                        <input
                          className="chat-input task-due"
                          type="text"
                          value={it.due_date}
                          disabled={busy}
                          placeholder={t("chat.task.duePlaceholder")}
                          onChange={(e) => updateItem(gi, ii, "due_date", e.target.value)}
                        />
                      </label>
                      <label className="task-mini">
                        <span className="task-mini-label">{t("chat.task.description")}</span>
                        <input
                          className="chat-input task-desc"
                          type="text"
                          value={it.description}
                          disabled={busy}
                          placeholder={t("chat.task.descriptionPlaceholder")}
                          onChange={(e) => updateItem(gi, ii, "description", e.target.value)}
                        />
                      </label>
                    </div>
                    <button
                      className="task-remove"
                      type="button"
                      title={t("chat.task.remove")}
                      aria-label={t("chat.task.remove")}
                      disabled={busy}
                      onClick={() => removeItem(gi, ii)}
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
              <button
                className="task-add-item"
                type="button"
                disabled={busy}
                onClick={() => addItem(gi)}
              >
                {t("chat.task.addItem")}
              </button>
            </div>
          ))}
        </div>
        <button
          className="task-add-group"
          type="button"
          disabled={busy}
          onClick={addGroup}
        >
          {t("chat.task.addGroup")}
        </button>

      <label className="task-field">
        <span className="task-label">{t("chat.task.reason")}</span>
        <textarea
          className="chat-input task-reason"
          rows={2}
          value={reason}
          disabled={busy}
          placeholder={t("chat.task.reasonPlaceholder")}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>

      <div className="pending-buttons">
        <button
          className="btn btn-approve"
          type="button"
          disabled={!canApprove}
          onClick={() => onApprove({ project: project.trim(), items: flattenGroups(groups) }, reason.trim())}
        >
          {t("chat.task.approve")}
        </button>
        <button className="btn btn-reject" type="button" disabled={busy} onClick={onReject}>
          {t("chat.reject")}
        </button>
      </div>
    </div>
  );
}
