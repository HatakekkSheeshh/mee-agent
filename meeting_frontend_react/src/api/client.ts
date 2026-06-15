// Typed fetch wrapper for Mee backend.
// Vite dev server (:8001) proxies /api → backend :8002, so all paths here use relative URLs.

import type {
  Meeting,
  MeetingDetail,
  MeetingMember,
  Recording,
  RecordingTranscript,
  CleanResponse,
  MoMJson,
  ProjectSummary,
  ChatSessionSummary,
  ChatStreamStep,
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

// ─── Celery task polling ─────────────────────────────────────────────
export type CeleryState =
  | "PENDING"
  | "STARTED"
  | "RETRY"
  | "SUCCESS"
  | "FAILURE"
  | "REVOKED";

export interface TaskStatusResponse {
  task_id: string;
  state: CeleryState;
  result?: {
    recording_id: string;
    meeting_id?: string;
    notes?: MoMJson;
    saved_paths?: { md?: string; memory_events?: number };
    memory_context_count?: number;
    error?: string;
  };
  error?: string;
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
  tasks: {
    /** Poll Celery task state by id. Returns PENDING/STARTED while running,
     * SUCCESS with `result` payload when done, FAILURE with `error` on fail. */
    status: (taskId: string) =>
      http<TaskStatusResponse>("GET", `/api/tasks/${taskId}`),
  },
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
    /** List members of a meeting — feeds Notta-style speaker dropdown. */
    listMembers: (id: string) =>
      http<{ meeting_id: string; members: MeetingMember[] }>(
        "GET", `/api/meetings/${id}/members`,
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
          /** Per-word timestamps (faster-whisper). Persisted on
           * transcript_segments.words for FE Notta word-accurate sync. */
          words?: { text: string; start: number; end: number }[] | null;
        }>;
        session_label?: string;
        replace?: boolean;
        duration_sec?: number | null;
        recording_id?: string | null;
        /** Per-cluster voice embeddings from PhoWhisper server. Stored on
         * recording.speaker_embeddings for later voiceprint matching. */
        cluster_embeddings?: Record<string, number[]> | null;
        /** Local-pyannote-only: base64 3s sample WAV per cluster. Backend
         * decodes + writes to output/<rid>/spk_<label>.wav so SpeakerMapper
         * can play a clip. PhoWhisper path leaves this null. */
        sample_audio_b64?: Record<string, string> | null;
        /** Chunked upload path: full cleaned WAV staged server-side. Backend
         * dispatches a Celery task to run pyannote on it and writes the
         * speaker_embeddings + sample paths back when done. */
        pending_diarize_path?: string | null;
      },
    ) =>
      http<{
        meeting_id: string;
        recording_id: string;
        segments_count: number;
        deleted_recordings?: number;
        /** Set when pending_diarize_path was provided + Celery is reachable.
         * FE polls /api/tasks/{id} → reloads meeting when SUCCESS. */
        diarize_task_id?: string | null;
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
    /** Stream the raw audio file for in-browser playback (Notta-style sync). */
    audioUrl: (id: string) => `/api/recordings/${id}/audio`,
    /** Attach (or replace) the source audio for a recording — used to recover
     * playback when the original transcribe path didn't persist the audio. */
    uploadAudio: async (id: string, file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`/api/recordings/${id}/audio`, { method: "POST", body: fd });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new ApiError(r.status, j.detail || r.statusText);
      }
      return r.json() as Promise<{ recording_id: string; audio_path: string; size_bytes: number }>;
    },
    /** Notta-style speaker rename: scope='current' overrides just this segment,
     * 'all' renames every segment that currently shares this row's label. */
    patchSegmentSpeaker: (
      id: string,
      index: number,
      speaker: string,
      scope: "current" | "all",
    ) =>
      http<{ renamed: number; scope: string; speaker: string }>(
        "PATCH",
        `/api/recordings/${id}/segment-speaker`,
        { index, speaker, scope },
      ),
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
    /** Enqueue MoM generation. Returns either:
     *   - `{task_id, status:"queued", mode:"celery"}` — FE polls /tasks/{id}
     *   - `{notes, ...}` (legacy inline shape) — broker down, ran inline
     * Caller should branch on the presence of `task_id`. */
    generateMom: (id: string, uiLang: string = "vi") =>
      http<
        | {
            task_id: string;
            recording_id: string;
            status: "queued";
            mode: "celery";
          }
        | {
            recording_id: string;
            meeting_id: string;
            notes: MoMJson;
            saved_paths: { md?: string; memory_events?: number };
            memory_context_count: number;
            mode: "inline";
          }
      >("POST", `/api/recordings/${id}/generate-mom?ui_lang=${encodeURIComponent(uiLang)}`),
    getMom: (id: string) =>
      http<{ recording_id: string; mom_json: MoMJson }>(
        "GET", `/api/recordings/${id}/mom`,
      ),
    downloadUrl: (id: string, fmt: "md" | "json" = "md") =>
      `/api/recordings/${id}/download?fmt=${fmt}`,
  },

  // ─── Transcribe upload (one-shot, no DB persistence) ────────────────
  transcribe: async (
    file: File,
    language = "vi",
    vocabHints = "",
    attendees = "",
    recordingId = "",
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("language", language);
    if (vocabHints) fd.append("vocab_hints", vocabHints);
    // Attendees feed into the Whisper prompt (`_build_whisper_prompt`) to bias
    // STT toward correct name spellings + diarization toward expected #speakers.
    if (attendees) fd.append("attendees", attendees);
    // Tell the backend WHICH recording owns this audio so it can persist the
    // file under output/audio/<recording_id>.<ext> and patch recording.audio_path.
    // Enables Notta-style in-browser playback via /api/recordings/{id}/audio.
    if (recordingId) fd.append("recording_id", recordingId);
    const r = await fetch("/api/transcribe", { method: "POST", body: fd });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new ApiError(r.status, j.detail || r.statusText);
    }
    return (await r.json()) as {
      text: string;
      chunked?: boolean;
      segments?: {
        speaker?: string;
        text: string;
        start?: number;
        end?: number;
        /** Per-word timestamps (faster-whisper only). [{text, start, end}] in
         * seconds. Forwarded by FE to /import-transcript → persisted on
         * transcript_segments.words. */
        words?: { text: string; start: number; end: number }[];
      }[];
      /** PhoWhisper-server only — 256-dim embedding per pyannote cluster. */
      cluster_embeddings?: Record<string, number[]>;
      /** Local-pyannote path — base64-encoded 3s WAV per cluster, surfaced
       * so we can ship them with the /diarize-result POST and let the
       * backend persist them for SpeakerMapper playback. */
      sample_audio_b64?: Record<string, string>;
      /** Chunked path — server-side relative path to the cleaned WAV staged
       * for async pyannote. FE forwards to /import-transcript so backend can
       * dispatch the diarize Celery task. */
      pending_diarize_path?: string | null;
    };
  },

  /** URL for the per-cluster 3s sample WAV stored under recording.speaker_sample_paths.
   * Returns a usable <audio src=…> URL; falls back to 404 when the recording
   * has no sample for that cluster (FE should hide the play button via
   * recording.speaker_samples list). */
  speakerSampleUrl: (recordingId: string, label: string) =>
    `/api/recordings/${recordingId}/speaker-sample/${encodeURIComponent(label)}`,

  // ─── Auth ──────────────────────────────────────────────────────────
  // Cookie flows: /auth/login + /auth/callback are full-page redirects, NOT
  // fetches — we let the browser navigate so the session cookie lands on the
  // same origin. /auth/me and /auth/logout are fetched after the browser is
  // already back on the React app.
  auth: {
    /** Get current user. 401 → not authenticated. */
    me: () =>
      http<{
        id: string;
        email: string;
        display_name: string | null;
        avatar_url: string | null;
        voice_enrolled: boolean;
        provider: "mock" | "microsoft";
      }>("GET", "/auth/me"),
    /** Clear server-side session cookie. */
    logout: () => http<{ ok: boolean }>("POST", "/auth/logout"),
    /** Full-page redirect to start the OAuth flow. `next` is bounced back to
     * after successful login (defaults to the current path so the user lands
     * back where they started). */
    loginUrl: (next: string = window.location.pathname) =>
      `/auth/login?next=${encodeURIComponent(next)}`,
  },

  // ─── Chat ──────────────────────────────────────────────────────────
  chat: {
    // Create a user-scoped session. No meeting binding — grounding is per-turn.
    createSession: (title?: string) =>
      http<{ id: string; meeting_id: string | null; title: string; created_at: string }>(
        "POST", "/api/chat/sessions", title ? { title } : {},
      ),
    // List the user's sessions (sidebar), most-recently-active first.
    listSessions: () =>
      http<ChatSessionSummary[]>("GET", "/api/chat/sessions"),
    // Session detail + messages (used when switching sessions in the sidebar).
    sessionDetail: (sessionId: string) =>
      http<{
        id: string;
        meeting_id: string | null;
        title: string | null;
        messages: Array<{
          id: string;
          role: string;
          content: { text?: string };
          created_at: string;
        }>;
      }>("GET", `/api/chat/sessions/${sessionId}`),
    // Remove a session permanently (hard delete + checkpoint purge).
    remove: (sessionId: string) =>
      http<{ status: string; session_id: string }>(
        "DELETE", `/api/chat/sessions/${sessionId}`,
      ),
    // Proactive kickoff: Mee speaks first on an empty thread. Returns the
    // greeting (already persisted as an agent message), or {reply:null,
    // skipped:true} if the thread already had messages. Never throws server-side.
    kickoff: (sessionId: string, role?: string) =>
      http<{ reply: string | null; role?: string | null; skipped?: boolean }>(
        "POST", `/api/chat/sessions/${sessionId}/kickoff`,
        role ? { role } : undefined,
      ),
    // Backend MessageSend expects `text` (NOT `message`); returns a status envelope.
    send: (sessionId: string, text: string, meetingId: string | null) =>
      http<ChatTurnResult>(
        "POST", `/api/chat/sessions/${sessionId}/messages`,
        { text, meeting_id: meetingId },
      ),
    // Streaming variant: SSE frames over a POST body. `onStep` fires per
    // progress event; resolves with the terminal frame mapped to the same
    // ChatTurnResult envelope as `send` (so callers can share applyResult).
    sendStream: async (
      sessionId: string,
      text: string,
      meetingId: string | null,
      onStep: (ev: ChatStreamStep) => void,
      signal?: AbortSignal,
    ): Promise<ChatTurnResult> => {
      const r = await fetch(`/api/chat/sessions/${sessionId}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, meeting_id: meetingId }),
        signal,
      });
      if (!r.ok || !r.body) {
        throw new ApiError(r.status, r.statusText || "stream failed");
      }
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let terminal: ChatTurnResult | null = null;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let ev: Record<string, unknown>;
          try {
            ev = JSON.parse(line.slice(6)) as Record<string, unknown>;
          } catch {
            continue; // skip malformed frame rather than killing the turn
          }
          if (ev.type === "step") {
            onStep(ev as unknown as ChatStreamStep);
          } else if (ev.type === "error") {
            throw new ApiError(500, String(ev.detail ?? "stream error"));
          } else if (ev.type === "complete") {
            terminal = {
              status: "complete",
              reply: String(ev.reply ?? ""),
              intent: ev.intent as string | undefined,
              tool_result: ev.tool_result,
            };
          } else if (ev.type === "interrupted") {
            terminal = ev as unknown as Extract<
              ChatTurnResult,
              { status: "interrupted" }
            >;
          }
        }
      }
      if (!terminal) throw new ApiError(500, "stream ended without a result");
      return terminal;
    },
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
