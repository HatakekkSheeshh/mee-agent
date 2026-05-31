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
  purpose?: string | null;
  venue?: string | null;
  date?: string | null;
  chaired_by?: string | null;
  noted_by?: string | null;
  attendees?: Attendee[] | null;
  status: string;
  has_summary: boolean;
  is_pinned?: boolean;
}

export interface Recording {
  id: string;
  session_label?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_sec?: number | null;
  status: string;
  segment_count: number;
  mom_json?: MoMJson | null;
  has_clean?: boolean;
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
  attendees?: string;
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
export interface RecordingTranscript {
  recording_id: string;
  meeting_id: string;
  session_label?: string | null;
  transcript: string;
  segment_count: number;
  duration_sec?: number | null;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface CleanResponse {
  recording_id: string;
  cached: boolean;
  clean_segments: { speaker?: string; text: string; tags?: string[] }[];
  /** User-edited HTML (TipTap output) if the user has touched this transcript. */
  edited_html?: string | null;
  edited_text?: string | null;
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
