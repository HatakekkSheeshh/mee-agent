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
}: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const [members, setMembers] = useState<MeetingMember[]>([]);
  const [currentSec, setCurrentSec] = useState(0);
  // Derived ms-based playhead used by word-level highlight and activeIdx.
  const currentMs = Math.floor(currentSec * 1000);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [openDropdownIdx, setOpenDropdownIdx] = useState<number | null>(null);
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
    closeTimerRef.current = window.setTimeout(() => setHoveredMember(null), 120);
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

  // Which segment contains the current audio time → active highlight.
  const activeIdx = useMemo(() => {
    const curMs = currentSec * 1000;
    for (let i = 0; i < cleanSegments.length; i++) {
      const s = segTimes[i].start_ms;
      const e = segTimes[i].end_ms ?? segTimes[i + 1]?.start_ms ?? null;
      if (s == null) continue;
      if (curMs >= s && (e == null || curMs < e)) return i;
    }
    return -1;
  }, [cleanSegments, segTimes, currentSec]);

  // Smooth-scroll the active segment into center view as it changes.
  useEffect(() => {
    if (activeIdx < 0 || !listRef.current) return;
    const row = listRef.current.querySelector(
      `[data-idx="${activeIdx}"]`,
    ) as HTMLElement | null;
    if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeIdx]);

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

  const seekTo = useCallback((sec: number) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = sec;
    setCurrentSec(sec);
    if (a.paused) a.play().catch(() => { /* user-gesture required */ });
  }, []);

  const skip = useCallback((deltaSec: number) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, Math.min(duration, a.currentTime + deltaSec));
  }, [duration]);

  const togglePlay = useCallback(() => {
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

  // Count blocks per speaker label — drives "Apply to all 'X' (N blocks)".
  const blocksByName = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of cleanSegments) {
      const name = resolveSpeakerName(s.speaker);
      m.set(name, (m.get(name) || 0) + 1);
    }
    return m;
  }, [cleanSegments, resolveSpeakerName]);

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
    idx: number,
    _clusterId: string,
    name: string,
    scope: "current" | "all",
  ) {
    try {
      await api.recordings.patchSegmentSpeaker(recordingId, idx, name, scope);
      onClusterMappingSaved();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      alert(`Lưu speaker lỗi: ${msg}`);
    } finally {
      setOpenDropdownIdx(null);
      setSearch("");
    }
  }

  // Filter members for the dropdown search. When the search is empty show
  // all members. Always append "use as guest" hint for typed-but-unmatched
  // names so the user can label non-members.
  const filteredMembers = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return members;
    return members.filter(
      (m) =>
        m.display_name.toLowerCase().includes(q) ||
        m.email.toLowerCase().includes(q),
    );
  }, [members, search]);

  const hasGuestOption = useMemo(() => {
    const q = search.trim();
    if (!q) return false;
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
          title="LLM clean lại từ raw"
        >
          ↻ Re-transcribe
        </button>
        <div className="notta-toolbar-hint">
          Click avatar → đổi speaker. Click vào dòng để tua audio đến đó.
        </div>
      </div>

      {/* Segments list (scrolls) */}
      <div ref={listRef} className="notta-list">
        {cleanSegments.map((seg, i) => {
          const rawSpk = seg.speaker || "";
          const name = resolveSpeakerName(rawSpk);
          const color = hashColor(name);
          const isActive = i === activeIdx;
          const startMs = segTimes[i].start_ms;
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
                      setOpenDropdownIdx(openDropdownIdx === i ? null : i);
                      setSearch("");
                    }}
                    aria-label={`Đổi speaker (${name})`}
                  >
                    <span className="notta-chip-initials">{initials(name)}</span>
                    <span className="notta-chip-name">{name}</span>
                    <span className="notta-chip-caret">▾</span>
                  </button>

                  {openDropdownIdx === i && (
                    <div
                      className="notta-dropdown"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        className="notta-dropdown-search"
                        placeholder="Search attendee"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        autoFocus
                      />
                      <div className="notta-dropdown-section-label">
                        Suggestions
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
                                    <span className="notta-dropdown-badge" title="Đã enroll voice">🎤</span>
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
                                Add "{search.trim()}" as guest
                              </span>
                              <span className="notta-dropdown-caret">›</span>
                            </div>
                          </div>
                        )}
                        {filteredMembers.length === 0 && !hasGuestOption && (
                          <div className="notta-dropdown-empty">
                            Không tìm thấy member.
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
                <div className="notta-row-text">
                  {(() => {
                    const text = seg.text || "";
                    // STRATEGY 1: real word timestamps from faster-whisper.
                    // We paired clean segment[i] with raw segment[rawIdx]
                    // via the same speaker-turn logic that produced segTimes,
                    // then use raw.words[] directly. Exact ms accuracy.
                    const rawIdx = segTimes[i].rawIdx;
                    const rawWords =
                      rawIdx != null ? rawSegments[rawIdx]?.words : null;
                    if (rawWords && rawWords.length > 0) {
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

                    // STRATEGY 2: approximate even-split. No real word ts
                    // (VNG MaaS / PhoWhisper word-ts disabled). Spread the
                    // segment's [start, end] evenly across visible words —
                    // visually-OK fallback.
                    const sMs = segTimes[i].start_ms;
                    const eMs = segTimes[i].end_ms;
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
            <span>Audio không tải được. Recording này chưa có file audio.</span>
            <label className="btn btn-ghost btn-xs" style={{ marginLeft: 8 }}>
              <span>📎 Gắn file audio</span>
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
                    alert(
                      `Upload audio lỗi: ${
                        err instanceof ApiError ? err.detail : (err as Error).message
                      }`,
                    );
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
          onLoadedMetadata={(e) =>
            setDuration((e.target as HTMLAudioElement).duration || 0)
          }
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
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
            title="Tua lùi 3 giây"
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
            title="Tua tới 3 giây"
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
       * fixed coordinates from the hovered member's bounding rect. */}
      {hoveredMember && openDropdownIdx != null && (() => {
        const i = openDropdownIdx;
        const seg = cleanSegments[i];
        const rawSpk = seg?.speaker || "";
        const currentName = resolveSpeakerName(rawSpk);
        const blocks = blocksByName.get(currentName) || 0;
        const targetName = hoveredMember.member.display_name;
        const r = hoveredMember.rect;
        return createPortal(
          <div
            className="notta-submenu-portal"
            style={{ top: r.top, left: r.right + 6 }}
            onMouseEnter={cancelClose}
            onMouseLeave={scheduleClose}
          >
            <button
              type="button"
              className="notta-dropdown-action"
              onClick={() => {
                applyName(i, rawSpk, targetName, "current");
                setHoveredMember(null);
              }}
            >
              Apply to current speaker
            </button>
            <button
              type="button"
              className="notta-dropdown-action"
              onClick={() => {
                applyName(i, rawSpk, targetName, "all");
                setHoveredMember(null);
              }}
            >
              Apply to all "{currentName}" ({blocks} blocks)
            </button>
          </div>,
          document.body,
        );
      })()}
    </div>
  );
}
