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

// ─── Transcript ────────────────────────────────────────────────────
export interface RawSegment {
  seq: number;
  text: string;
  speaker?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
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
  clean_segments?: { speaker?: string; text: string; tags?: string[] }[];
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
