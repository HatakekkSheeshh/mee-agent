// Typed fetch wrapper for Mee backend.
// Vite dev server proxies /api → :8001, so all paths here use relative URLs.

import type {
  Meeting,
  MeetingDetail,
  RecordingTranscript,
  CleanResponse,
  MoMJson,
  ProjectSummary,
  ChatMessage,
} from "../types/api";

class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

async function http<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// ─── Meetings ──────────────────────────────────────────────────────
export const api = {
  meetings: {
    list: () => http<Meeting[]>("GET", "/api/meetings"),
    get: (id: string) => http<MeetingDetail>("GET", `/api/meetings/${id}`),
    create: (data: Partial<Meeting>) =>
      http<Meeting>("POST", "/api/meetings", data),
    patch: (id: string, data: Partial<Meeting>) =>
      http<Meeting>("PATCH", `/api/meetings/${id}`, data),
    remove: (id: string) =>
      http<{ deleted: boolean }>("DELETE", `/api/meetings/${id}`),
    generateProjectSummary: (id: string) =>
      http<{ meeting_id: string; summary: ProjectSummary }>(
        "POST", `/api/meetings/${id}/generate-project-summary`,
      ),
    importTranscript: (
      meetingId: string,
      data: {
        text: string;
        session_label?: string;
        replace?: boolean;
        duration_sec?: number | null;
        recording_id?: string | null;
      },
    ) =>
      http<{
        meeting_id: string;
        recording_id: string;
        segments_count: number;
        deleted_recordings?: number;
      }>("POST", `/api/meetings/${meetingId}/import-transcript`, data),
  },

  // ─── Recordings ────────────────────────────────────────────────────
  recordings: {
    create: (meetingId: string, sessionLabel?: string) =>
      http<{ id: string; session_label?: string }>(
        "POST", `/api/meetings/${meetingId}/recordings`,
        { session_label: sessionLabel },
      ),
    rename: (id: string, label: string) =>
      http<{ recording_id: string; session_label: string }>(
        "PATCH", `/api/recordings/${id}`, { session_label: label },
      ),
    transcript: (id: string) =>
      http<RecordingTranscript>("GET", `/api/recordings/${id}/transcript`),
    clean: (id: string, regenerate = false) =>
      http<CleanResponse>(
        "POST",
        `/api/recordings/${id}/clean${regenerate ? "?regenerate=true" : ""}`,
      ),
    saveCleanEdited: (id: string, html: string, text: string) =>
      http<{ recording_id: string; edited_chars: number }>(
        "PATCH", `/api/recordings/${id}/clean-edited`, { html, text },
      ),
    end: (id: string) =>
      http<{ id: string; status: string; duration_sec?: number }>(
        "POST", `/api/recordings/${id}/end`,
      ),
    remove: (id: string) =>
      http<{ recording_id: string; deleted: boolean }>(
        "DELETE", `/api/recordings/${id}`,
      ),
    generateMom: (id: string) =>
      http<{
        recording_id: string;
        meeting_id: string;
        notes: MoMJson;
        saved_paths: { md?: string; memory_events?: number };
        memory_context_count: number;
      }>("POST", `/api/recordings/${id}/generate-mom`),
    getMom: (id: string) =>
      http<{ recording_id: string; mom_json: MoMJson }>(
        "GET", `/api/recordings/${id}/mom`,
      ),
    downloadUrl: (id: string, fmt: "md" | "json" = "md") =>
      `/api/recordings/${id}/download?fmt=${fmt}`,
  },

  // ─── Transcribe upload (one-shot, no DB persistence) ────────────────
  transcribe: async (file: File, language = "vi", vocabHints = "") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("language", language);
    if (vocabHints) fd.append("vocab_hints", vocabHints);
    const r = await fetch("/api/transcribe", { method: "POST", body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new ApiError(r.status, j.detail || r.statusText);
    }
    return (await r.json()) as { text: string; chunked?: boolean };
  },

  // ─── Chat ──────────────────────────────────────────────────────────
  chat: {
    createSession: (meetingId: string) =>
      http<{ id: string; meeting_id: string }>(
        "POST", "/api/chat/sessions", { meeting_id: meetingId },
      ),
    send: (sessionId: string, message: string) =>
      http<ChatMessage>(
        "POST", `/api/chat/sessions/${sessionId}/messages`, { message },
      ),
    resume: (
      sessionId: string,
      approval: { approved: boolean; reason?: string },
    ) =>
      http<ChatMessage>(
        "POST", `/api/chat/sessions/${sessionId}/resume`, approval,
      ),
  },
};

export { ApiError };
