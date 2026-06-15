// NottaCleanView — Notta-style transcript editor combining:
//   • Segments list with avatar + speaker chip (click → dropdown of members)
//   • Per-segment timestamp (derived from raw_segments by index pairing)
//   • Active highlight as audio.currentTime crosses each segment
//   • Sticky audio player at pane bottom: slider, play/pause, ±3s skip, rate
//   • Double-click row → seek to that segment's start
//
// Why not 1 huge CleanEditor: this view is read-mostly with quick edits via
// dropdown. CleanEditor is HTML contentEditable focused on prose editing.
// We can integrate later — for now the two are mutually exclusive.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "../api/client";
import { useApp } from "../store/AppContext";
import type { CleanResponse, MeetingMember, RawSegment } from "../types/api";

type CleanSeg = NonNullable<CleanResponse["clean_segments"]>[number];

interface Props {
  recordingId: string;
  meetingId: string;
  cleanSegments: CleanSeg[];
  /** Raw transcribe segments — used to lend timestamps to clean segments
   * by positional pairing. Empty for live-record before diarize finishes. */
  rawSegments: RawSegment[];
  /** SPEAKER_NN → friendly name. From /clean response, mutated by dropdown. */
  clusterMapping: Record<string, string>;
  onRegenerate: () => void;
  onClusterMappingSaved: () => void;
  busy: boolean;
  /** Hint set by TranscriptPane while an SSE stream is mid-flight. Triggers
   * a per-word reveal animation on the LAST (newest) segment — words pop
   * in one-by-one giving the Notta "live dictation" feel during upload.
   * Once `busy` flips back to false, animation stops. */
  streaming?: boolean;
  /** One-shot signal from TranscriptPane after a fresh upload finishes.
   * On the next mount/update where this is true, audio auto-plays from
   * t=0 and karaoke reveal kicks in. Uses a ref internally so the same
   * recording doesn't re-trigger karaoke if the user switches away and
   * back. */
  autoPlayKaraoke?: boolean;
  /** Names of attendees marked on THIS recording (from
   * recording.attendees[].name). When non-empty, the speaker chip
   * dropdown only suggests members whose display_name matches one of
   * these — keeps the picker scoped to who was actually in the room
   * rather than every member of the project. Falls back to all members
   * when empty so an unconfigured recording isn't locked out. */
  attendees?: string[];
}

// Stable per-name color so the same person always gets the same avatar tint.
const HUES = [142, 200, 280, 30, 350, 165, 50, 320];
function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return `hsl(${HUES[Math.abs(h) % HUES.length]}, 65%, 60%)`;
}
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
function fmtTime(sec: number): string {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

export function NottaCleanView({
  recordingId,
  meetingId,
  cleanSegments,
  rawSegments,
  clusterMapping,
  onRegenerate,
  onClusterMappingSaved,
  busy,
  streaming = false,
  autoPlayKaraoke = false,
  attendees = [],
}: Props) {
  const { t, audioOutputDeviceId } = useApp();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [members, setMembers] = useState<MeetingMember[]>([]);
  const [currentSec, setCurrentSec] = useState(0);
  // Derived ms-based playhead used by word-level highlight and activeIdx.
  const currentMs = Math.floor(currentSec * 1000);
  // (Per-segment reveal animation removed — playback now shows full
  // transcript with audio-synced word highlight only. Setter retained
  // via assignment-only state for future live-record reveal in Phase C.)
  const [, setRevealCount] = useState<Record<number, number>>({});
  // ── Karaoke mode ────────────────────────────────────────────────
  // When streaming transitions from true → false (an upload just
  // finished), we auto-play the audio + enable karaokeMode. In that
  // mode, words with start_time > currentSec are hidden (opacity 0)
  // so they "appear" in sync with the audio as it speaks them. User
  // can disable via the karaoke button — and any pause/seek auto-
  // disables it too (signal that user wants to read freely).
  const [karaokeMode, setKaraokeMode] = useState(false);
  // High-water mark: once a word has been revealed by playback, it
  // stays visible even if the user seeks backward. Without this,
  // seeking back during karaoke would hide already-spoken words.
  const [maxRevealedSec, setMaxRevealedSec] = useState(0);
  // ── Inline edit mode ────────────────────────────────────────────
  // Toggled via the ✎ button in the toolbar. When on, each block's
  // text turns into a contentEditable area; blur autosaves via
  // patchSegmentText. Word-level highlight is suppressed in edit mode
  // because we no longer know word boundaries for user-typed text.
  const [editMode, setEditMode] = useState(false);
  // Per-segment "saving in flight" indicator keyed by raw segment seq.
  const [savingSeqs, setSavingSeqs] = useState<Set<number>>(new Set());
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [openDropdownIdx, setOpenDropdownIdx] = useState<number | null>(null);
  // Direction the speaker dropdown should open relative to its chip.
  // 'down' = anchor below (default); 'up' = anchor above the chip. Flipped
  // when the chip is near the bottom of the viewport (close to the sticky
  // audio player) so the dropdown isn't clipped by the footer.
  const [dropdownDir, setDropdownDir] = useState<"up" | "down">("down");
  const [search, setSearch] = useState("");
  const [audioError, setAudioError] = useState(false);
  // Hovered member's submenu: rendered via portal at document.body so
  // ancestor scroll clipping never hides it. {memberKey, anchorRect, member}.
  const [hoveredMember, setHoveredMember] = useState<{
    memberKey: string;
    rect: DOMRect;
    member: MeetingMember | { display_name: string; isGuest: true };
  } | null>(null);
  // Timeout for delayed-close (user can move cursor from row → submenu without
  // a gap killing the submenu).
  const closeTimerRef = useRef<number | null>(null);
  const scheduleClose = useCallback(() => {
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = window.setTimeout(() => setHoveredMember(null), 300);
  }, []);
  const cancelClose = useCallback(() => {
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  // Load meeting members on mount / meeting change.
  useEffect(() => {
    let cancelled = false;
    api.meetings
      .listMembers(meetingId)
      .then((r) => { if (!cancelled) setMembers(r.members); })
      .catch(() => { if (!cancelled) setMembers([]); });
    return () => { cancelled = true; };
  }, [meetingId]);

  // Map clean segment index → timestamp.
  //
  // STRATEGY: pair "speaker turns", not raw segments.
  //
  // The cleaner LLM merges consecutive same-speaker raw segments into one
  // clean segment (a "turn"). So `clean.length << raw.length` in general,
  // and positional pairing of clean[i] ↔ raw[i] is wrong — it lands at a
  // raw segment from much EARLIER in the audio (this is the "audio jumps
  // to blocks above" bug the user reported).
  //
  // Fix: collapse rawSegments into turns FIRST (consecutive same-speaker
  // raw segs merged with start=first.start, end=last.end), then pair
  // clean[i] ↔ raw_turn[i]. Turn boundaries match because cleaner doesn't
  // merge across speaker changes.
  //
  // Speaker comparison goes through clusterMapping because clean.speaker
  // is the friendly name ("Đại") while raw.speaker is the cluster id
  // ("SPEAKER_00"). We compare friendly-on-friendly.
  //
  // Fallback: when raw has no timestamps (VNG Whisper upload — no
  // diarization), evenly distribute over the audio duration. The user
  // still gets click-to-seek + active highlight at approximate boundaries.
  const segTimes = useMemo(() => {
    // `rawIdx` (third field) is the index into rawSegments whose `words[]`
    // backs this clean segment for word-accurate highlight. Null when no
    // real word ts available — caller falls back to even-distribute.
    const out: { start_ms: number | null; end_ms: number | null; rawIdx: number | null }[] = [];
    const haveRaw = rawSegments.some((s) => s.start_ms != null);

    if (haveRaw) {
      // SHORT-CIRCUIT: when the caller passed rawSegments as cleanSegments
      // (the "trust raw over LLM" path in TranscriptPane), the two arrays
      // have the same length and the same per-row text. Turn-pairing
      // would collapse 5 consecutive same-speaker raw segs into 1 turn
      // and then run out of turns → most rows end up with null start_ms
      // → click-to-seek silently does nothing. Direct positional pairing
      // is exact in this case.
      if (rawSegments.length === cleanSegments.length) {
        return rawSegments.map((r, i) => ({
          start_ms: r.start_ms ?? null,
          end_ms: r.end_ms ?? null,
          rawIdx: r.start_ms != null ? i : null,
        }));
      }

      // Build turns: consecutive same-speaker raw segs → one span. We also
      // remember `rawIdx` = the FIRST raw segment in the turn — when STT
      // also returned word timestamps (faster-whisper), the FE can pull
      // word_ts straight from `rawSegments[rawIdx].words` for exact sync.
      // (Most meetings have 1 raw seg per turn anyway since faster-whisper
      // already groups by speaker on the server side.)
      type Turn = {
        speaker: string | null;
        start_ms: number;
        end_ms: number;
        rawIdx: number;
      };
      const turns: Turn[] = [];
      for (let r = 0; r < rawSegments.length; r++) {
        const seg = rawSegments[r];
        if (seg.start_ms == null) continue;
        const friendly = clusterMapping[seg.speaker || ""] || seg.speaker || null;
        const end = seg.end_ms ?? seg.start_ms;
        const last = turns[turns.length - 1];
        if (last && last.speaker === friendly) {
          last.end_ms = end;
        } else {
          turns.push({ speaker: friendly, start_ms: seg.start_ms, end_ms: end, rawIdx: r });
        }
      }

      // Pair clean[i] with turns[turnIdx]. When speakers don't match,
      // advance turnIdx forward (e.g. cleaner dropped a turn because it
      // was empty/silent).
      let turnIdx = 0;
      for (let i = 0; i < cleanSegments.length; i++) {
        const cleanSpk = cleanSegments[i].speaker || null;
        // Skip turns whose speaker doesn't line up — until we find one
        // that does, or run out.
        while (
          turnIdx < turns.length &&
          cleanSpk != null &&
          turns[turnIdx].speaker != null &&
          turns[turnIdx].speaker !== cleanSpk
        ) {
          turnIdx++;
        }
        if (turnIdx < turns.length) {
          out.push({
            start_ms: turns[turnIdx].start_ms,
            end_ms: turns[turnIdx].end_ms,
            rawIdx: turns[turnIdx].rawIdx,
          });
          turnIdx++;
        } else {
          out.push({ start_ms: null, end_ms: null, rawIdx: null });
        }
      }
      return out;
    }

    // Fallback: distribute over <audio> duration proportional to text
    // length. Even-split was bad UX because short turns ("Vâng.") got the
    // same time slice as paragraphs — highlight raced ahead of the actual
    // audio for short turns and lagged behind for long ones.
    if (duration <= 0 || cleanSegments.length === 0) {
      return cleanSegments.map(() => ({ start_ms: null, end_ms: null, rawIdx: null }));
    }
    const lens = cleanSegments.map((s) => Math.max(1, (s.text || "").length));
    const totalChars = lens.reduce((a, b) => a + b, 0);
    const totalMs = duration * 1000;
    let cursor = 0;
    return lens.map((len) => {
      const start = Math.round((cursor / totalChars) * totalMs);
      cursor += len;
      const end = Math.round((cursor / totalChars) * totalMs);
      return { start_ms: start, end_ms: end, rawIdx: null };
    });
  }, [cleanSegments, rawSegments, clusterMapping, duration]);

  // ── Display blocks: merge consecutive same-speaker rows ──────────
  // In view mode, Notta-style behaviour groups successive segments
  // from the same speaker into one block (matches the screenshot the
  // user shared — each row is one whole turn). In edit mode we KEEP
  // them split so the user can edit individual segments and their
  // `transcript_segments.edited_text` rows save cleanly.
  type DisplayBlock = {
    speaker: string;
    text: string;            // joined for merged blocks, original for single
    startMs: number | null;
    endMs: number | null;
    /** Indices into `cleanSegments` that contributed (length 1 in edit). */
    cleanIndices: number[];
    /** All `RawSegment.words` flattened in playback order. */
    words: { text: string; start: number; end: number }[];
  };
  const displayBlocks = useMemo<DisplayBlock[]>(() => {
    const wordsFor = (i: number) => {
      const rIdx = segTimes[i]?.rawIdx;
      const r = rIdx != null ? rawSegments[rIdx] : null;
      return r?.words || [];
    };
    if (editMode) {
      // Edit mode → one block per cleanSegment (no merging).
      return cleanSegments.map((s, i) => ({
        speaker: s.speaker || "",
        text: s.text || "",
        startMs: segTimes[i]?.start_ms ?? null,
        endMs: segTimes[i]?.end_ms ?? null,
        cleanIndices: [i],
        words: wordsFor(i),
      }));
    }
    // View mode → merge consecutive same-speaker.
    const blocks: DisplayBlock[] = [];
    for (let i = 0; i < cleanSegments.length; i++) {
      const seg = cleanSegments[i];
      const last = blocks[blocks.length - 1];
      const segWords = wordsFor(i);
      if (last && (last.speaker || "") === (seg.speaker || "")) {
        // Append to existing block.
        last.text = (last.text + " " + (seg.text || "")).trim();
        last.endMs = segTimes[i]?.end_ms ?? last.endMs;
        last.cleanIndices.push(i);
        if (segWords.length) last.words.push(...segWords);
      } else {
        blocks.push({
          speaker: seg.speaker || "",
          text: seg.text || "",
          startMs: segTimes[i]?.start_ms ?? null,
          endMs: segTimes[i]?.end_ms ?? null,
          cleanIndices: [i],
          words: [...segWords],
        });
      }
    }
    return blocks;
  }, [cleanSegments, segTimes, rawSegments, editMode]);

  // Which display block contains the current audio time → active highlight.
  const activeIdx = useMemo(() => {
    const curMs = currentSec * 1000;
    for (let i = 0; i < displayBlocks.length; i++) {
      const s = displayBlocks[i].startMs;
      const e =
        displayBlocks[i].endMs ?? displayBlocks[i + 1]?.startMs ?? null;
      if (s == null) continue;
      if (curMs >= s && (e == null || curMs < e)) return i;
    }
    return -1;
  }, [displayBlocks, currentSec]);

  // Smooth-scroll the active segment into center view as it changes.
  useEffect(() => {
    if (activeIdx < 0 || !listRef.current) return;
    const row = listRef.current.querySelector(
      `[data-idx="${activeIdx}"]`,
    ) as HTMLElement | null;
    if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeIdx]);

  // ── Streaming word-by-word reveal ─────────────────────────────────
  // When `streaming` is true (an SSE upload is mid-flight), animate words
  // appearing one-by-one in the LAST segment — it's the one that just
  // landed and the user expects to see "typing" into. We don't animate
  // older segments — that would make the whole transcript flicker as
  // each new segment lands.
  //
  // Also: auto-scroll to the newest segment so the user keeps seeing
  // fresh words instead of having to scroll manually.
  useEffect(() => {
    if (!streaming || cleanSegments.length === 0) return;
    const lastIdx = cleanSegments.length - 1;
    const seg = cleanSegments[lastIdx];
    // Word count for the last segment — prefer real STT words, fall back
    // to whitespace-split text.
    const rawIdx = segTimes[lastIdx]?.rawIdx;
    const rawWords = rawIdx != null ? rawSegments[rawIdx]?.words : null;
    const wordCount = (rawWords?.length || (seg?.text || "").split(/\s+/).filter(Boolean).length);
    if (wordCount === 0) return;

    setRevealCount((prev) => {
      // If this segment is already revealed past wordCount, no-op.
      const current = prev[lastIdx] ?? 0;
      if (current >= wordCount) return prev;
      return { ...prev, [lastIdx]: 0 };  // reset to 0 to start animation
    });

    let cancelled = false;
    let i = 0;
    const tick = () => {
      if (cancelled) return;
      i += 1;
      setRevealCount((prev) => ({ ...prev, [lastIdx]: i }));
      if (i < wordCount) {
        // 80ms per word ≈ typing rhythm. Adjust for very long segments
        // (>20 words) to finish in ~1.5s so user isn't waiting.
        const delay = wordCount > 20 ? Math.max(40, 1500 / wordCount) : 80;
        window.setTimeout(tick, delay);
      }
    };
    const handle = window.setTimeout(tick, 50);

    // Auto-scroll newest segment into view.
    if (listRef.current) {
      const row = listRef.current.querySelector(
        `[data-idx="${lastIdx}"]`,
      ) as HTMLElement | null;
      if (row) row.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
    // Only re-run when a NEW segment is added (length changes) — not on
    // every cleanSegments mutation (would restart current animation).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cleanSegments.length, streaming]);

  // When streaming ends, ensure all segments are fully revealed (clear
  // any partial caps so the audio-playback word-highlight path takes over).
  useEffect(() => {
    if (streaming) return;
    setRevealCount({});
  }, [streaming]);

  // Karaoke auto-trigger: arm exactly once per false→true edge of
  // autoPlayKaraoke. Using a previous-value ref instead of a per-
  // recording flag lets a SECOND upload to the same recording re-fire
  // karaoke (TranscriptPane resets freshUpload=false at upload start
  // for the edge to land). Also avoids re-firing on re-renders where
  // autoPlayKaraoke stays true.
  const prevAutoPlayRef = useRef(false);
  useEffect(() => {
    const justArmed = autoPlayKaraoke && !prevAutoPlayRef.current;
    prevAutoPlayRef.current = autoPlayKaraoke;
    if (!justArmed) return;

    const haveWords = rawSegments.some((r) => r.words && r.words.length > 0);
    if (!haveWords) return;

    const a = audioRef.current;
    if (!a) return;

    const startKaraoke = () => {
      // No more "karaoke hide future" — just auto-play from t=0 after a
      // fresh upload. Word-level highlight (driven by currentMs) gives
      // the synced visual feedback. User can read ahead, scrub, Cmd+F.
      a.currentTime = 0;
      a.play().catch((err) => {
        console.warn(
          "[autoplay] blocked — click ▶ to start playback",
          err,
        );
      });
    };

    if (a.readyState >= 1) {
      // Metadata already loaded → play immediately.
      startKaraoke();
    } else {
      // Wait for the audio source to advertise duration + timing data.
      // Without this, currentTime=0 may not stick and play() races the
      // first network byte.
      a.addEventListener("loadedmetadata", startKaraoke, { once: true });
      return () => a.removeEventListener("loadedmetadata", startKaraoke);
    }
  }, [autoPlayKaraoke, rawSegments, recordingId]);

  // (Recording-change reset removed — prevAutoPlayRef edge-tracking
  // already handles cross-recording re-arming since TranscriptPane flips
  // freshUpload false→true on each upload start.)

  // Track the furthest the playhead has reached during karaoke. Once a
  // word has been revealed, it stays visible — seeking backward doesn't
  // hide it.
  useEffect(() => {
    if (!karaokeMode) return;
    if (currentSec > maxRevealedSec) setMaxRevealedSec(currentSec);
  }, [currentSec, karaokeMode, maxRevealedSec]);

  // ── rAF-driven currentTime polling ─────────────────────────────────
  // <audio>.onTimeUpdate fires only ~4 times/sec (250ms gap), which
  // makes word-level karaoke highlight visibly lag the audio. Poll
  // audioRef.currentTime every animation frame instead — that's ~60fps,
  // so the highlight stays glued to the audio. We only spin the loop
  // while playing (no point burning frames while paused).
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const a = audioRef.current;
      if (a) setCurrentSec(a.currentTime);
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [playing]);

  // Route audio output to the user-selected speaker via setSinkId.
  // setSinkId is Chrome/Edge only; we no-op (silently) on unsupported
  // browsers like Firefox. Empty string = browser/OS default.
  useEffect(() => {
    const a = audioRef.current as (HTMLAudioElement & {
      setSinkId?: (id: string) => Promise<void>;
    }) | null;
    if (!a?.setSinkId) return;
    a.setSinkId(audioOutputDeviceId || "").catch(() => {
      /* selected device may have been unplugged — fall back silently */
    });
  }, [audioOutputDeviceId]);

  // Close the speaker dropdown when clicking outside it.
  useEffect(() => {
    if (openDropdownIdx == null) return;
    function onDown(e: MouseEvent) {
      const t = e.target as HTMLElement;
      if (!t.closest(".notta-dropdown") && !t.closest(".notta-chip")) {
        setOpenDropdownIdx(null);
        setSearch("");
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [openDropdownIdx]);

  // Any explicit user navigation cancels karaoke — they want to read
  // the transcript freely, not wait for audio to "speak" the rest.
  const cancelKaraoke = useCallback(() => setKaraokeMode(false), []);

  const seekTo = useCallback((sec: number) => {
    cancelKaraoke();
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = sec;
    setCurrentSec(sec);
    if (a.paused) a.play().catch(() => { /* user-gesture required */ });
  }, [cancelKaraoke]);

  const skip = useCallback((deltaSec: number) => {
    cancelKaraoke();
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, Math.min(duration, a.currentTime + deltaSec));
  }, [duration, cancelKaraoke]);

  const togglePlay = useCallback(() => {
    // DO NOT cancelKaraoke here — pause via the play button should FREEZE
    // the karaoke state (unrevealed blocks stay hidden, words stop
    // revealing). Resume picks up where we left off. Only explicit
    // navigation (seek slider, ±3s skip, clicking a future block) cancels.
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => { /* ignore */ });
    else a.pause();
  }, []);

  const changeRate = useCallback((newRate: number) => {
    const a = audioRef.current;
    if (a) a.playbackRate = newRate;
    setRate(newRate);
  }, []);

  // Resolve display name for a SPEAKER_NN cluster id.
  const resolveSpeakerName = useCallback(
    (rawSpeaker: string | undefined | null): string => {
      if (!rawSpeaker) return "?";
      // Cluster_mapping is the source of truth; falls back to raw label.
      return clusterMapping[rawSpeaker] || rawSpeaker;
    },
    [clusterMapping],
  );

  // Count VISUAL blocks per speaker — drives "Apply to all 'X' (N blocks)"
  // text in the rename submenu. We count displayBlocks rather than
  // individual cleanSegments so the number matches what the user actually
  // sees on screen (a merged turn = 1 block in the count).
  const blocksByName = useMemo(() => {
    const m = new Map<string, number>();
    for (const b of displayBlocks) {
      const name = resolveSpeakerName(b.speaker);
      m.set(name, (m.get(name) || 0) + 1);
    }
    return m;
  }, [displayBlocks, resolveSpeakerName]);

  // Apply a chosen name to either the current segment or every segment
  // sharing this row's speaker label. Both paths now use PATCH segment-speaker
  // — the backend's `scope='all'` matches by speaker LABEL (not cluster_id),
  // so it works even after the cluster has been renamed once.
  //
  // We deliberately skip the legacy `voiceprints.bind` call here: it expects
  // a raw cluster id like "SPEAKER_00", but `seg.speaker` is the friendly
  // name after the first rename — passing that as cluster_id breaks the
  // embedding lookup. Voiceprint persistence happens separately via the
  // SpeakerMapper tool when the user explicitly wants to enroll a voice.
  async function applyName(
    blockIdx: number,
    rawSpk: string,
    name: string,
    scope: "current" | "all",
  ) {
    console.log("[applyName] called", { blockIdx, name, scope, recordingId });
    // Resolve which clean_segments rows this block represents. In view
    // mode a block may have merged several rows of the same speaker;
    // "Apply to current speaker" means rename every row in that block.
    // "Apply to all" still applies cross-block via the backend's label-
    // matching logic — we only need to send one PATCH for it.
    const block = displayBlocks[blockIdx];
    const indices = block?.cleanIndices?.length
      ? block.cleanIndices
      : [blockIdx];
    console.log("[applyName] resolved indices", indices, "block:", block);
    if (!recordingId) {
      console.error("[applyName] no recordingId — abort");
      alert(t("notta.error.noRecording"));
      return;
    }
    // Resolve cluster_id for the backend. Three sources, in priority:
    //  1) Explicit `cluster_id` stamped on the anchor cleanSegment
    //     (new recordings after the cluster-id stamp fix).
    //  2) `rawSpk` from the block — when TranscriptPane fed raw
    //     segments AS clean (rawAsClean mode for accurate timestamps),
    //     the block.speaker IS the raw SPEAKER_NN, i.e. the cluster
    //     id itself. This handles the index-out-of-range case because
    //     backend then matches segments by cluster_id even when FE's
    //     indices point past clean_segments.length.
    //  3) null — backend stays in index-only mode (legacy path).
    const anchorCleanIdx = indices[0];
    const anchorSeg = cleanSegments[anchorCleanIdx] as
      | { cluster_id?: string }
      | undefined;
    let clusterId: string | null = anchorSeg?.cluster_id || null;
    if (!clusterId && rawSpk && rawSpk.startsWith("SPEAKER_")) {
      clusterId = rawSpk;
    }

    async function patchOne(idx: number, sc: "current" | "all") {
      return api.recordings.patchSegmentSpeaker(
        recordingId, idx, name, sc, clusterId,
      );
    }
    try {
      if (scope === "all") {
        const r = await patchOne(indices[0], "all");
        console.log("[applyName] PATCH scope=all OK", r);
      } else {
        const results = await Promise.all(indices.map((idx) => patchOne(idx, "current")));
        console.log("[applyName] PATCH scope=current OK", results);
      }
      onClusterMappingSaved();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      console.error("[applyName] PATCH failed", e);
      alert(t("notta.error.saveSpeaker", { msg }));
    } finally {
      setOpenDropdownIdx(null);
      setSearch("");
    }
  }

  // Narrow `members` to people the user actually marked as attendees on
  // this recording (via Members panel checkboxes → recording.attendees).
  // If the attendees list is empty we fall back to ALL project members so
  // an unconfigured recording isn't locked out of speaker labelling.
  const recordingMembers = useMemo(() => {
    if (!attendees || attendees.length === 0) return members;
    const allow = new Set(attendees.map((n) => n.trim().toLowerCase()).filter(Boolean));
    if (allow.size === 0) return members;
    const scoped = members.filter((m) =>
      allow.has(m.display_name.trim().toLowerCase()),
    );
    // Defensive fallback: if the intersection is empty (attendees names
    // don't match any member, e.g. external guests typed in), show all
    // members so user has SOMETHING to pick.
    return scoped.length > 0 ? scoped : members;
  }, [members, attendees]);

  // Filter members for the dropdown search. When the search is empty show
  // the recording-scoped pool. Always append "use as guest" hint for
  // typed-but-unmatched names so the user can label non-members.
  const filteredMembers = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return recordingMembers;
    return recordingMembers.filter(
      (m) =>
        m.display_name.toLowerCase().includes(q) ||
        m.email.toLowerCase().includes(q),
    );
  }, [recordingMembers, search]);

  const hasGuestOption = useMemo(() => {
    const q = search.trim();
    if (!q) return false;
    // Guest check matches against the FULL project member list, not just
    // attendees — if the typed name already exists as a member but isn't
    // an attendee yet, we don't want to offer "Add as guest" since they
    // can just be ticked into attendees from the Members panel.
    return !members.some(
      (m) => m.display_name.toLowerCase() === q.toLowerCase(),
    );
  }, [members, search]);

  return (
    <div className="notta-view">
      {/* Top regenerate bar */}
      <div className="notta-toolbar">
        <button
          className="btn btn-ghost btn-xs"
          type="button"
          onClick={onRegenerate}
          disabled={busy}
          title={t("notta.regenTitle")}
        >
          {t("notta.regenBtn")}
        </button>
        <label
          className={`toggle-switch${editMode ? " on" : ""}`}
          title={editMode ? t("notta.editMode.on") : t("notta.editMode.off")}
        >
          <input
            type="checkbox"
            checked={editMode}
            onChange={(e) => setEditMode(e.target.checked)}
          />
          <span className="toggle-switch-track">
            <span className="toggle-switch-thumb" />
          </span>
          <span className="toggle-switch-label">{t("notta.editMode.label")}</span>
        </label>
      </div>

      {/* Segments list (scrolls). Iterates `displayBlocks` (consecutive
       * same-speaker rows merged in view mode, kept split in edit mode). */}
      <div ref={listRef} className="notta-list">
        {displayBlocks.map((block, i) => {
          const seg = { speaker: block.speaker, text: block.text };
          const rawSpk = block.speaker || "";
          const name = resolveSpeakerName(rawSpk);
          const color = hashColor(name);
          const isActive = i === activeIdx;
          const startMs = block.startMs;
          const tsLabel = startMs != null ? fmtTime(startMs / 1000) : "--:--";

          return (
            <div
              key={i}
              data-idx={i}
              className={`notta-row${isActive ? " active" : ""}`}
              onClick={() => startMs != null && seekTo(startMs / 1000)}
              role="button"
              tabIndex={0}
            >
              <div className="notta-row-ts">{tsLabel}</div>
              <div className="notta-row-main">
                <div className="notta-row-head">
                  <button
                    type="button"
                    className="notta-chip"
                    style={{ background: color }}
                    onClick={(e) => {
                      e.stopPropagation();
                      const next = openDropdownIdx === i ? null : i;
                      if (next != null) {
                        // Flip the dropdown above the chip when the chip
                        // sits within ~360px of the viewport bottom (the
                        // dropdown can grow to ~330px; the audio player
                        // floats at the bottom). Falls back to 'down'
                        // when there's room or the chip is unmeasurable.
                        const rect = (
                          e.currentTarget as HTMLElement
                        ).getBoundingClientRect();
                        const spaceBelow = window.innerHeight - rect.bottom;
                        const spaceAbove = rect.top;
                        const NEEDED = 340;
                        setDropdownDir(
                          spaceBelow < NEEDED && spaceAbove > spaceBelow
                            ? "up"
                            : "down",
                        );
                      }
                      setOpenDropdownIdx(next);
                      setSearch("");
                    }}
                    aria-label={t("notta.changeSpeaker", { name })}
                  >
                    <span className="notta-chip-initials">{initials(name)}</span>
                    <span className="notta-chip-name">{name}</span>
                    <span className="notta-chip-caret">▾</span>
                  </button>

                  {openDropdownIdx === i && (
                    <div
                      className={`notta-dropdown notta-dropdown-${dropdownDir}`}
                      onClick={(e) => e.stopPropagation()}
                      onWheel={(e) => {
                        // Trap wheel inside the dropdown so scrolling
                        // the member list doesn't bubble up and scroll
                        // the transcript behind it. The dropdown's
                        // own .notta-dropdown-list handles overflow.
                        e.stopPropagation();
                      }}
                    >
                      <input
                        className="notta-dropdown-search"
                        placeholder={t("notta.dropdown.searchPlaceholder")}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        autoFocus
                      />
                      <div className="notta-dropdown-section-label">
                        {t("notta.dropdown.suggestions")}
                      </div>
                      <div className="notta-dropdown-list">
                        {filteredMembers.map((m) => {
                          const isCurrent =
                            m.display_name.toLowerCase() === name.toLowerCase();
                          // Hover reveals a submenu rendered via PORTAL at
                          // document.body — escapes all ancestor `overflow`
                          // clipping (the list scrolls; ancestors hide). The
                          // submenu opens to the right of this row.
                          return (
                            <div
                              key={m.user_id}
                              className={`notta-dropdown-member${isCurrent ? " current" : ""}`}
                              onMouseEnter={(e) => {
                                cancelClose();
                                setHoveredMember({
                                  memberKey: m.user_id,
                                  rect: (e.currentTarget as HTMLElement).getBoundingClientRect(),
                                  member: m,
                                });
                              }}
                              onMouseLeave={scheduleClose}
                            >
                              <div className="notta-dropdown-member-head">
                                <span
                                  className="notta-dropdown-avatar"
                                  style={{ background: hashColor(m.display_name) }}
                                >
                                  {initials(m.display_name)}
                                </span>
                                <span className="notta-dropdown-name">
                                  {m.display_name}
                                  {m.voice_enrolled && (
                                    <span className="notta-dropdown-badge" title={t("notta.dropdown.voiceEnrolled")}>🎤</span>
                                  )}
                                </span>
                                {isCurrent && (
                                  <span className="notta-dropdown-check">✓</span>
                                )}
                                <span className="notta-dropdown-caret">›</span>
                              </div>
                            </div>
                          );
                        })}
                        {hasGuestOption && (
                          <div
                            className="notta-dropdown-member guest"
                            onMouseEnter={(e) => {
                              cancelClose();
                              setHoveredMember({
                                memberKey: `guest:${search.trim()}`,
                                rect: (e.currentTarget as HTMLElement).getBoundingClientRect(),
                                member: { display_name: search.trim(), isGuest: true },
                              });
                            }}
                            onMouseLeave={scheduleClose}
                          >
                            <div className="notta-dropdown-member-head">
                              <span
                                className="notta-dropdown-avatar"
                                style={{ background: "var(--text-faint)" }}
                              >
                                +
                              </span>
                              <span className="notta-dropdown-name">
                                {t("notta.dropdown.addGuest", { name: search.trim() })}
                              </span>
                              <span className="notta-dropdown-caret">›</span>
                            </div>
                          </div>
                        )}
                        {filteredMembers.length === 0 && !hasGuestOption && (
                          <div className="notta-dropdown-empty">
                            {t("notta.dropdown.empty")}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
                <div
                  className={`notta-row-text${editMode ? " editable" : ""}${
                    (() => {
                      const cIdx = block.cleanIndices[0];
                      const rIdx = segTimes[cIdx]?.rawIdx;
                      const seq = rIdx != null ? rawSegments[rIdx]?.seq : undefined;
                      return seq != null && savingSeqs.has(seq) ? " saving" : "";
                    })()
                  }`}
                  // In edit mode, the text turns into a contentEditable
                  // <div>. We deliberately render the PLAIN seg.text (not
                  // word spans) so the user can place a cursor anywhere
                  // and edit naturally. Blur fires save.
                  contentEditable={editMode}
                  suppressContentEditableWarning
                  onClick={editMode ? (e) => e.stopPropagation() : undefined}
                  onBlur={
                    editMode
                      ? async (e) => {
                          const newText = (e.currentTarget.textContent || "").trim();
                          // In edit mode each block is one cleanSegment
                          // (no merging) so cleanIndices[0] is the index
                          // into rawSegments — pull its `seq` for the
                          // PATCH path.
                          const cIdx = block.cleanIndices[0];
                          const rIdx = segTimes[cIdx]?.rawIdx;
                          const rawSeg = rIdx != null ? rawSegments[rIdx] : null;
                          const seq = rawSeg?.seq;
                          if (seq == null) return;
                          if (newText === (seg.text || "").trim()) return; // no change
                          setSavingSeqs((prev) => {
                            const next = new Set(prev);
                            next.add(seq);
                            return next;
                          });
                          try {
                            await api.recordings.patchSegmentText(
                              recordingId, seq, newText,
                            );
                          } catch (err) {
                            const msg =
                              err instanceof ApiError ? err.detail : (err as Error).message;
                            alert(t("notta.error.saveSegment", { msg }));
                          } finally {
                            setSavingSeqs((prev) => {
                              const next = new Set(prev);
                              next.delete(seq);
                              return next;
                            });
                          }
                        }
                      : undefined
                  }
                >
                  {editMode ? seg.text : (() => {
                    const text = seg.text || "";
                    // STRATEGY 1: real word timestamps. Block-level words[]
                    // already concatenates every contributing raw segment's
                    // words in playback order, so merged speaker-turns
                    // render one continuous run with the current word
                    // highlighted regardless of which sub-segment it
                    // originally came from.
                    const rawWords = block.words;
                    if (rawWords && rawWords.length > 0) {
                      // Show ALL words — playback UX. The currently-spoken
                      // word gets a subtle background highlight as audio
                      // plays through; user can read ahead, search with
                      // Cmd+F, click any word to seek. The previous
                      // "karaoke hide future" pattern was wrong for
                      // playback (read-ahead impossible) — keep it only
                      // for live-record mode (Phase C) where future
                      // doesn't exist yet.
                      const activeWordIdx = isActive
                        ? rawWords.findIndex(
                            (w) =>
                              currentMs >= w.start * 1000 &&
                              currentMs < w.end * 1000,
                          )
                        : -1;
                      return rawWords.map((w, idx) => (
                        <span
                          key={idx}
                          className={`notta-word${idx === activeWordIdx ? " active" : ""}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            seekTo(w.start);
                          }}
                          title={`${w.start.toFixed(2)}s`}
                        >
                          {w.text}
                          {idx < rawWords.length - 1 && " "}
                        </span>
                      ));
                    }

                    // STRATEGY 2: approximate even-split over the whole
                    // merged block's [start, end]. Used when STT didn't
                    // emit per-word timing (VNG MaaS Whisper path).
                    const sMs = block.startMs;
                    const eMs = block.endMs;
                    if (sMs == null || eMs == null || eMs <= sMs) {
                      return text;
                    }
                    const parts = text.split(/(\s+)/);
                    const words = parts.filter((p) => p.trim().length > 0);
                    if (words.length === 0) return text;
                    const perWordMs = (eMs - sMs) / words.length;
                    const activeWordIdx =
                      isActive
                        ? Math.min(
                            words.length - 1,
                            Math.max(0, Math.floor((currentMs - sMs) / perWordMs)),
                          )
                        : -1;
                    let wordCounter = 0;
                    return parts.map((part, idx) => {
                      if (part.trim().length === 0) {
                        return <span key={idx}>{part}</span>;
                      }
                      const w = wordCounter++;
                      const isActiveWord = w === activeWordIdx;
                      return (
                        <span
                          key={idx}
                          className={`notta-word${isActiveWord ? " active" : ""}`}
                        >
                          {part}
                        </span>
                      );
                    });
                  })()}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Sticky audio player at bottom */}
      <div className="notta-player">
        {audioError && (
          <div className="notta-player-error">
            <span>{t("notta.audio.cannotLoad")}</span>
            <label className="btn btn-ghost btn-xs" style={{ marginLeft: 8 }}>
              <span>{t("notta.audio.attach")}</span>
              <input
                type="file"
                accept="audio/*"
                hidden
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  try {
                    await api.recordings.uploadAudio(recordingId, f);
                    setAudioError(false);
                    // Force <audio> to refetch via cache-buster query param.
                    if (audioRef.current) {
                      audioRef.current.src =
                        api.recordings.audioUrl(recordingId) + `?t=${Date.now()}`;
                      audioRef.current.load();
                    }
                  } catch (err) {
                    const msg =
                      err instanceof ApiError ? err.detail : (err as Error).message;
                    alert(t("notta.audio.uploadError", { msg }));
                  } finally {
                    e.target.value = "";
                  }
                }}
              />
            </label>
          </div>
        )}
        <audio
          ref={audioRef}
          src={api.recordings.audioUrl(recordingId)}
          preload="metadata"
          onTimeUpdate={(e) =>
            setCurrentSec((e.target as HTMLAudioElement).currentTime)
          }
          onLoadedMetadata={(e) => {
            const a = e.target as HTMLAudioElement;
            // Chromium MediaRecorder writes webm WITHOUT a Duration tag in
            // the EBML header — audio.duration reports Infinity until the
            // browser scans to the end of the file. Workaround: seek past
            // the end, wait for timeupdate, then snap back to 0 — the
            // browser fills duration during the scan.
            if (!isFinite(a.duration) || a.duration === 0) {
              const onUpd = () => {
                if (isFinite(a.duration) && a.duration > 0) {
                  a.removeEventListener("timeupdate", onUpd);
                  a.currentTime = 0;
                  setDuration(a.duration);
                }
              };
              a.addEventListener("timeupdate", onUpd);
              try { a.currentTime = 1e6; } catch { /* some browsers throw */ }
            } else {
              setDuration(a.duration);
            }
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => {
            // Per user's spec: pause = FREEZE — keep karaoke mode on so
            // unrevealed blocks stay hidden and the word reveal stops at
            // the current playhead. Resuming play continues from where
            // we left off. (Old behaviour reveal-all-on-pause was
            // jarring — the transcript would suddenly dump everything.)
            setPlaying(false);
          }}
          onEnded={() => {
            // Audio finished naturally — drop karaoke so the tail of the
            // transcript shows (some word.start may be slightly past the
            // actual audio duration due to DTW rounding).
            setKaraokeMode(false);
          }}
          onError={() => setAudioError(true)}
          style={{ display: "none" }}
        />
        <input
          className="notta-player-slider"
          type="range"
          min={0}
          max={Math.max(duration, 1)}
          step={0.1}
          value={currentSec}
          onChange={(e) => seekTo(parseFloat(e.target.value))}
        />
        <div className="notta-player-row">
          <select
            className="notta-player-rate"
            value={rate}
            onChange={(e) => changeRate(parseFloat(e.target.value))}
          >
            {[0.5, 0.75, 1, 1.25, 1.5, 2].map((r) => (
              <option key={r} value={r}>{r}x</option>
            ))}
          </select>
          <button
            type="button"
            className="notta-player-btn"
            onClick={() => skip(-3)}
            title={t("notta.skipBack")}
          >
            ↺3
          </button>
          <button
            type="button"
            className="notta-player-btn notta-player-play"
            onClick={togglePlay}
          >
            {playing ? "❚❚" : "▶"}
          </button>
          <button
            type="button"
            className="notta-player-btn"
            onClick={() => skip(3)}
            title={t("notta.skipForward")}
          >
            3↻
          </button>
          <div className="notta-player-time">
            {fmtTime(currentSec)} / {fmtTime(duration)}
          </div>
        </div>
      </div>

      {/* Portal-floating submenu — escapes any ancestor overflow clipping
       * (the segments list + dropdown card both scroll). Positioned with
       * fixed coordinates from the hovered member's bounding rect.
       *
       * We use onMouseDown (not onClick) on the action buttons because
       * onClick was racing with the unmount: as soon as the mouse moves
       * down onto the button, no new mouseLeave fires on the dropdown
       * member row, but the scheduleClose timer from an earlier
       * mouseLeave could land between mousedown and click and unmount
       * the portal. onMouseDown fires earlier in the cycle. */}
      {hoveredMember && openDropdownIdx != null && (() => {
        const i = openDropdownIdx;
        // openDropdownIdx is a *display block* index, NOT a clean_segments
        // index. Translate via displayBlocks → first clean index → seg.
        const block = displayBlocks[i];
        const cleanIdx = block?.cleanIndices?.[0] ?? i;
        const seg = cleanSegments[cleanIdx];
        const rawSpk = seg?.speaker || "";
        const currentName = resolveSpeakerName(rawSpk);
        const blocks = blocksByName.get(currentName) || 0;
        const targetName = hoveredMember.member.display_name;
        const r = hoveredMember.rect;
        console.log("[submenu] render", { i, cleanIdx, rawSpk, currentName, targetName, blocks });
        return createPortal(
          <div
            className="notta-submenu-portal"
            style={{ top: r.top, left: r.right + 2 }}
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            <button
              type="button"
              className="notta-dropdown-action"
              onMouseDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log("[submenu] Apply current clicked");
                applyName(i, rawSpk, targetName, "current");
                setHoveredMember(null);
              }}
            >
              {t("notta.applyCurrent")}
            </button>
            <button
              type="button"
              className="notta-dropdown-action"
              onMouseDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                console.log("[submenu] Apply all clicked");
                applyName(i, rawSpk, targetName, "all");
                setHoveredMember(null);
              }}
            >
              {t("notta.applyAll", { name: currentName, n: blocks })}
            </button>
          </div>,
          document.body,
        );
      })()}
    </div>
  );
}
