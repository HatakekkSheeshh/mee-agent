// TypeScript types matching backend responses.
// Source of truth: meeting/api/meetings.py + meeting/api/chat.py.

export interface Attendee {
  name: string;
  department?: string;
  title?: string;
}

export interface Meeting {
  id: string;
  title: string;
  /** Project-level default vocab. Appended with recording.vocab_hints at runtime
   * to feed Whisper prompt + cleaner LLM (fix Vietnamese phonetic
   * mistranscriptions like "chất manh tây sành" → "segmentation"). */
  vocab_hints?: string | null;
  /** Project default STT/LLM model logical IDs ("whisper"/"phowhisper" and
   * "gemma"/"qwen"/"gpt-oss"). Recording-level fields override these. */
  stt_model?: string | null;
  llm_model?: string | null;
  /** MoM output language ("vi" / "en"). NULL = inherit UI lang at gen time. */
  mom_language?: string | null;
  status: string;
  has_summary: boolean;
  is_pinned?: boolean;
}

export interface Recording {
  id: string;
  session_label?: string | null;
  /** Per-meeting-event metadata (moved from project in migration 0012). */
  title?: string | null;
  purpose?: string | null;
  date?: string | null;
  venue?: string | null;
  chaired_by?: string | null;
  noted_by?: string | null;
  attendees?: Attendee[] | null;
  /** Session-specific vocab additions (appended to project default at runtime). */
  vocab_hints?: string | null;
  /** Per-recording override of STT/LLM model. NULL = inherit from meeting. */
  stt_model?: string | null;
  llm_model?: string | null;
  /** Per-recording MoM language override. NULL = inherit meeting. */
  mom_language?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_sec?: number | null;
  status: string;
  segment_count: number;
  mom_json?: MoMJson | null;
  has_clean?: boolean;
  /** Cluster ids that have a stored 3s voice sample on disk. Empty/missing
   * means the recording was diarized before sample extraction or pyannote
   * couldn't slice usable clips. SpeakerMapper shows a play button only
   * for clusters present in this list. */
  speaker_samples?: string[];
}

export interface MeetingDetail extends Meeting {
  recordings: Recording[];
  project_summary_json?: ProjectSummary | null;
}

// ─── MoM JSON shape (per recording) ────────────────────────────────
export interface ActionItem {
  item: string;
  pic?: string | null;
  deadline?: string | null;
}

export interface MoMJson {
  title?: string;
  purpose?: string;
  date?: string;
  venue?: string;
  chaired_by?: string;
  noted_by?: string;
  /** MoM-LLM output is inconsistent across runs: sometimes a comma-separated
   * string, sometimes a list of {name,title,department} (copied from
   * meeting.attendees JSONB). MoMPane normalizes to a display string. */
  attendees?: string | { name?: string; title?: string; department?: string }[] | null;
  summary?: string;
  agenda_items?: { topic_no?: number; agenda: string; description?: string }[];
  decisions?: (string | { text: string; by?: string })[];
  action_items?: ActionItem[];
  blockers?: (string | { text: string; by?: string })[];
  commitments?: (string | { text: string; by?: string })[];
}

// ─── Project summary (timeline) ────────────────────────────────────
export interface DecisionTimelineEntry {
  recording_id: string;
  session_label: string;
  date: string | null;
  decisions: string[];
}

export interface ProjectSummary {
  project_title: string;
  session_count: number;
  decisions_timeline: DecisionTimelineEntry[];
  narrative: string;
  generated_at: string;
}

// ─── Meeting members (Notta-style speaker dropdown) ───────────────
export interface MeetingMember {
  user_id: string;
  email: string;
  display_name: string;
  avatar_url?: string | null;
  voice_enrolled: boolean;
  role: "owner" | "editor" | "viewer";
}

// ─── Transcript ────────────────────────────────────────────────────
export interface WordTimestamp {
  text: string;
  start: number;  // absolute seconds
  end: number;    // absolute seconds
}

/** Server-Sent Events from /api/transcribe/stream — drives Notta-style
 * progressive transcript rendering. Each event is one SSE frame. */
export type StreamEvent =
  | { type: "meta"; duration: number; language: string }
  | {
      type: "diarize";
      turns: Array<{ speaker: string; start: number; end: number }>;
      embeddings: Record<string, number[]>;
    }
  | {
      type: "segment";
      speaker: string;
      text: string;
      start: number;
      end: number;
      words: WordTimestamp[];
    }
  | { type: "done"; segments_count: number }
  | { type: "error"; detail: string };

export interface RawSegment {
  seq: number;
  text: string;
  speaker?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  /** Per-word timestamps from STT backends that support word_timestamps
   * (faster-whisper). NULL when STT doesn't return them (VNG MaaS, etc).
   * FE Notta view uses these for word-accurate highlight; falls back to
   * even-distribute approximation when NULL. */
  words?: WordTimestamp[] | null;
  /** True once the user edited this segment's text. The stored `words` are
   * raw STT tokens that no longer match the edited text, so the Notta view
   * renders `text` instead of word spans for edited segments. */
  edited?: boolean;
}

export interface RecordingTranscript {
  recording_id: string;
  meeting_id: string;
  session_label?: string | null;
  transcript: string;
  /** Structured per-segment data with speaker + timestamps when available
   * (PhoWhisper diarized upload). Empty array for legacy / live-record data. */
  segments: RawSegment[];
  segment_count: number;
  duration_sec?: number | null;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface CleanResponse {
  recording_id: string;
  cached: boolean;
  /** Populated only on cache hit (cached=true) OR inline fallback path
   * (when RabbitMQ unreachable). When the cleaner is dispatched to Celery
   * this is empty — FE polls `task_id` and re-fetches /clean on SUCCESS. */
  clean_segments?: { speaker?: string; text: string; tags?: string[]; cluster_id?: string }[];
  /** LLM-inferred cluster → name mapping. Verified entries (voice-matched)
   * are listed in `pre_mapped_clusters`. */
  cluster_mapping?: Record<string, string>;
  /** Cluster ids that were resolved via voiceprint DB cosine match (✓ trusted). */
  pre_mapped_clusters?: string[];
  /** Cluster ids that have stored embeddings — can be saved as voiceprints.
   * Missing clusters = audio uploaded before Phase 2 OR audio too short. */
  available_clusters?: string[];
  /** User-edited HTML (TipTap output) if the user has touched this transcript. */
  edited_html?: string | null;
  edited_text?: string | null;
  /** Set when the cleaner was dispatched to Celery. FE polls /api/tasks/{id}
   * and re-calls /clean on SUCCESS (which then hits the DB cache). */
  task_id?: string;
  status?: "queued" | "running" | "done";
  mode?: "celery" | "inline";
}

// ─── Voiceprints (zero-shot speaker ID) ───────────────────────────
export interface Voiceprint {
  id: string;
  name: string;
  sample_count: number;
  last_seen_at?: string | null;
  created_at?: string | null;
}

// ─── Chat ──────────────────────────────────────────────────────────
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  pending_action?: {
    tool: string;
    args: Record<string, unknown>;
  } | null;
}

/** A HITL action the chat graph paused on, awaiting user approve/reject. */
export interface PendingAction {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  rationale?: string | null;
  description?: string | null;
  /** pm-agent HITL kind: "need_approval" (issues to confirm) |
   * "need_more_info" (free-text question the user must answer). */
  kind?: "need_approval" | "need_more_info" | "pm_error" | null;
  /** need_more_info: the prompt text (markdown) to show the user. */
  prompt?: string | null;
  /** need_approval: the issues pm-agent wants to create/update. */
  issues?: Record<string, unknown>[] | null;
  /** pm-agent thread (task) id this pause belongs to — shown on the card. */
  task_id?: string | null;
}

/** Envelope returned by POST /messages and /pending-actions/{id}/approve|reject. */
export type ChatTurnResult =
  | { status: "complete"; reply: string; intent?: string; tool_result?: unknown }
  | { status: "interrupted"; pending_action_id: string; pending_action: PendingAction }
  | { status: "executed"; reply: string; tool_result?: unknown }
  | { status: "rejected"; reply: string };

/** Progress frame from the SSE stream (POST /messages/stream) while the graph runs. */
export interface ChatStreamStep {
  type: "step";
  step: "context" | "classify" | "tool_call" | "tool_done" | "pm";
  /** classify: the detected intent. */
  intent?: string;
  /** tool_call: names of the tools the agent is about to run. */
  tools?: string[];
}
