// Typed fetch wrapper for Mee backend.
// Vite dev server proxies /api → :8001, so all paths here use relative URLs.

import type {
  Meeting,
  MeetingDetail,
  Recording,
  RecordingTranscript,
  CleanResponse,
  MoMJson,
  ProjectSummary,
  ChatTurnResult,
  Voiceprint,
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

// ─── Model registry (STT + LLM profiles for the picker) ──────────────
export interface ModelProfile {
  id: string;
  label: string;
  description: string;
  /** False when the profile's env vars (e.g. GPT_OSS_BASE_URL) are missing.
   * Picking it would silently fall back to the legacy default — UI should
   * disable / badge the option. */
  configured: boolean;
}
export interface ModelsResponse {
  stt: ModelProfile[];
  llm: ModelProfile[];
  default_stt: string;
  default_llm: string;
}

// ─── Meetings ──────────────────────────────────────────────────────
export const api = {
  models: {
    list: () => http<ModelsResponse>("GET", "/api/models"),
  },
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
        /** Optional structured segments (PhoWhisper response). When present,
         * backend uses them — preserves speaker tag + timestamp per segment.
         * Falls back to splitting `text` when absent (legacy / live record). */
        segments?: Array<{
          text: string;
          speaker?: string | null;
          start?: number | null;
          end?: number | null;
        }>;
        session_label?: string;
        replace?: boolean;
        duration_sec?: number | null;
        recording_id?: string | null;
        /** Per-cluster voice embeddings from PhoWhisper server. Stored on
         * recording.speaker_embeddings for later voiceprint matching. */
        cluster_embeddings?: Record<string, number[]> | null;
      },
    ) =>
      http<{
        meeting_id: string;
        recording_id: string;
        segments_count: number;
        deleted_recordings?: number;
      }>("POST", `/api/meetings/${meetingId}/import-transcript`, data),
  },

  // ─── Voiceprints (zero-shot speaker ID) ───────────────────────────
  voiceprints: {
    list: () => http<Voiceprint[]>("GET", "/api/voiceprints"),
    /** Bind a recording's cluster_id → name → save embedding to user's DB. */
    bind: (recordingId: string, clusterId: string, name: string) =>
      http<{ id: string; name: string; sample_count: number; cluster_id: string }>(
        "POST", `/api/recordings/${recordingId}/voiceprints`,
        { cluster_id: clusterId, name },
      ),
    rename: (id: string, name: string) =>
      http<{ id: string; name: string }>(
        "PATCH", `/api/voiceprints/${id}`, { name },
      ),
    remove: (id: string) =>
      http<{ deleted: boolean }>("DELETE", `/api/voiceprints/${id}`),
  },

  // ─── Recordings ────────────────────────────────────────────────────
  recordings: {
    create: (meetingId: string, sessionLabel?: string) =>
      http<{ id: string; session_label?: string }>(
        "POST", `/api/meetings/${meetingId}/recordings`,
        { session_label: sessionLabel },
      ),
    /** Patch per-recording metadata (title, purpose, date, attendees, vocab…). */
    patch: (id: string, data: Partial<Recording>) =>
      http<{
        recording_id: string;
        session_label?: string | null;
        title?: string | null;
      }>("PATCH", `/api/recordings/${id}`, data),
    /** Poll background clean status — FE shows progress indicator. */
    cleanStatus: (id: string) =>
      http<{
        recording_id: string;
        status: "idle" | "running" | "done";
        has_clean: boolean;
        in_flight: boolean;
        progress: null | {
          phase: "cleaning" | "saving";
          current_chunk: number;
          total_chunks: number;
          started_at_ms: number;
          raw_chars: number;
        };
      }>("GET", `/api/recordings/${id}/clean-status`),
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
    generateMom: (id: string, uiLang: string = "vi") =>
      http<{
        recording_id: string;
        meeting_id: string;
        notes: MoMJson;
        saved_paths: { md?: string; memory_events?: number };
        memory_context_count: number;
      }>("POST", `/api/recordings/${id}/generate-mom?ui_lang=${encodeURIComponent(uiLang)}`),
    getMom: (id: string) =>
      http<{ recording_id: string; mom_json: MoMJson }>(
        "GET", `/api/recordings/${id}/mom`,
      ),
    downloadUrl: (id: string, fmt: "md" | "json" = "md") =>
      `/api/recordings/${id}/download?fmt=${fmt}`,
  },

  // ─── Transcribe upload (one-shot, no DB persistence) ────────────────
  transcribe: async (file: File, language = "vi", vocabHints = "", attendees = "") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("language", language);
    if (vocabHints) fd.append("vocab_hints", vocabHints);
    // Attendees feed into the Whisper prompt (`_build_whisper_prompt`) to bias
    // STT toward correct name spellings + diarization toward expected #speakers.
    if (attendees) fd.append("attendees", attendees);
    const r = await fetch("/api/transcribe", { method: "POST", body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new ApiError(r.status, j.detail || r.statusText);
    }
    return (await r.json()) as {
      text: string;
      chunked?: boolean;
      segments?: { speaker?: string; text: string; start?: number; end?: number }[];
      /** PhoWhisper-server only — 256-dim embedding per pyannote cluster. */
      cluster_embeddings?: Record<string, number[]>;
    };
  },

  // ─── Chat ──────────────────────────────────────────────────────────
  chat: {
    createSession: (meetingId: string) =>
      http<{ id: string; meeting_id: string | null; title: string; created_at: string }>(
        "POST", "/api/chat/sessions", { meeting_id: meetingId },
      ),
    // Backend MessageSend expects `text` (NOT `message`); returns a status envelope.
    send: (sessionId: string, text: string) =>
      http<ChatTurnResult>(
        "POST", `/api/chat/sessions/${sessionId}/messages`, { text },
      ),
    // HITL resume hits /pending-actions/{id}/approve|reject (there is no /resume route).
    approve: (
      actionId: string,
      body: {
        edited_args?: Record<string, unknown>;
        reason?: string;
        // pm-agent: free-text answer to need_more_info, or the approval verb.
        text?: string;
        approval_action?: string;
      } = {},
    ) =>
      http<ChatTurnResult>(
        "POST", `/api/chat/pending-actions/${actionId}/approve`, body,
      ),
    reject: (actionId: string, reason?: string) =>
      http<ChatTurnResult>(
        "POST", `/api/chat/pending-actions/${actionId}/reject`, { reason },
      ),
    // Clear a session in place: wipes its messages + pending + checkpoint, keeps
    // the session id (and meeting binding), so the agent re-grounds on a clean thread.
    clear: (sessionId: string) =>
      http<{ status: string; session_id: string }>(
        "POST", `/api/chat/sessions/${sessionId}/clear`,
      ),
  },
};

export { ApiError };
