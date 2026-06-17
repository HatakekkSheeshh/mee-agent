// Conversation Insights — Notta-style analytics for a recording.
//
// Slides in from the right when the floating-rail "Conversation ratio"
// button is toggled. All metrics computed FE-side from
// /api/recordings/{id}/transcript segments — no extra backend needed.
//
// Metrics surfaced:
//   - Total silence vs speaking duration (stacked bar)
//   - Per-speaker:
//       • talk ratio (% of total speech time)
//       • monologue count (consecutive turns by same speaker, counted once)
//       • longest monologue (sum of consecutive turn durations)
//       • words-per-minute (when word timestamps available)
//       • mini timeline (bars where this speaker spoke)
import { useEffect, useMemo, useState } from "react";
import { useApp } from "../store/AppContext";
import { api } from "../api/client";
import type { RawSegment } from "../types/api";

interface SpeakerStats {
  speaker: string;
  totalMs: number;
  talkRatio: number; // 0..1
  monologues: number;
  longestMonologueMs: number;
  wpm: number | null;
  turns: Array<{ startMs: number; endMs: number }>;
}

export function InsightsPane() {
  const { currentRecordingId, currentMeeting, insightsOpen, toggleInsights, t } = useApp();
  const [segments, setSegments] = useState<RawSegment[]>([]);
  const [clusterMapping, setClusterMapping] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [updated, setUpdated] = useState<Date | null>(null);

  // Fetch BOTH raw transcript (for accurate timestamps + words) AND
  // clean cluster_mapping (for friendly speaker labels like "Đại"
  // instead of raw cluster ids like SPEAKER_01). The raw transcript
  // alone would surface SPEAKER_NN — not what the user sees in the
  // Clean view, which uses cluster_mapping for display.
  useEffect(() => {
    if (!insightsOpen || !currentRecordingId) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.recordings.transcript(currentRecordingId),
      api.recordings.clean(currentRecordingId, false).catch(() => null),
    ])
      .then(([transcriptRes, cleanRes]) => {
        if (cancelled) return;
        setSegments(transcriptRes.segments || []);
        // clean endpoint returns either {clean_segments, cluster_mapping}
        // (cache hit) or {task_id, status:"queued"} (dispatched). We
        // only need cluster_mapping; safe to read either shape.
        const cm = (cleanRes && "cluster_mapping" in cleanRes)
          ? (cleanRes.cluster_mapping || {})
          : {};
        setClusterMapping(cm);
        setUpdated(new Date());
      })
      .catch(() => { /* swallow — empty state */ })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [insightsOpen, currentRecordingId]);

  const currentRec = currentMeeting?.recordings.find((r) => r.id === currentRecordingId);
  const totalDurationMs = (currentRec?.duration_sec || 0) * 1000;

  // ── Compute metrics ─────────────────────────────────────────────────
  const { perSpeaker, speakingMs, silenceMs } = useMemo(() => {
    return computeStats(segments, totalDurationMs, clusterMapping);
  }, [segments, totalDurationMs, clusterMapping]);

  if (!insightsOpen) return null;

  return (
    <aside className="pane pane-insights">
      <div className="pane-header">
        <span className="pane-title">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}>
            <path d="M21 12a9 9 0 1 1-9-9" />
            <path d="M21 12A9 9 0 0 0 12 3v9z" fill="currentColor" fillOpacity="0.15" />
          </svg>
          {t("insights.title")}
        </span>
        <div className="pane-meta">
          {updated && (
            <span className="mono small" style={{ color: "var(--text-mute)" }}>
              {t("insights.updated", {
                time: updated.toLocaleTimeString(t("insights.locale")),
              })}
            </span>
          )}
          <button
            className="icon-btn icon-btn-sm"
            type="button"
            title={t("insights.close")}
            onClick={toggleInsights}
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      <div className="pane-content insights-content">
        {!currentRecordingId ? (
          <EmptyMsg
            title={t("insights.emptyNoRecording.title")}
            sub={t("insights.emptyNoRecording.sub")}
          />
        ) : loading ? (
          <EmptyMsg
            title={t("insights.computing.title")}
            sub={t("insights.computing.sub")}
          />
        ) : segments.length === 0 ? (
          <EmptyMsg
            title={t("insights.noData.title")}
            sub={t("insights.noData.sub")}
          />
        ) : (
          <>
            {/* Silence vs Speaking duration bar */}
            <section className="insights-section">
              <div className="insights-section-title">{t("insights.silenceTotal")}</div>
              <div className="insights-card">
                <div className="silence-bar">
                  <div
                    className="silence-bar-fill speaking"
                    style={{ width: `${pct(speakingMs, speakingMs + silenceMs)}%` }}
                    title={`${t("insights.speaking")} ${fmtTime(speakingMs)}`}
                  />
                  <div
                    className="silence-bar-fill silent"
                    style={{ width: `${pct(silenceMs, speakingMs + silenceMs)}%` }}
                    title={`${t("insights.silent")} ${fmtTime(silenceMs)}`}
                  />
                  <div className="silence-bar-label">
                    {fmtTime(silenceMs)}
                  </div>
                </div>
                <div className="silence-legend">
                  <span><span className="dot silent" /> {t("insights.silent")} {fmtTime(silenceMs)}</span>
                  <span><span className="dot speaking" /> {t("insights.speaking")} {fmtTime(speakingMs)}</span>
                </div>
              </div>
            </section>

            {/* Per-speaker deep dive */}
            <section className="insights-section">
              <div className="insights-section-title">
                {t("insights.deepDive")}
                <span className="insights-badge">
                  {perSpeaker.length}{" "}
                  {perSpeaker.length === 1
                    ? t("insights.speakerOne")
                    : t("insights.speakerMany")}
                </span>
              </div>
              <div className="insights-cards">
                {perSpeaker.map((s) => (
                  <SpeakerCard key={s.speaker} stats={s} totalDurationMs={totalDurationMs} />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </aside>
  );
}

// ─── Per-speaker card ──────────────────────────────────────────────
function SpeakerCard({ stats, totalDurationMs }: { stats: SpeakerStats; totalDurationMs: number }) {
  const { t } = useApp();
  const [expanded, setExpanded] = useState(true);
  const color = hashColor(stats.speaker);
  const initial = (stats.speaker || "?").trim().charAt(0).toUpperCase();
  // Healthy talk ratio between 15% – 60% for a multi-speaker meeting.
  // Outside: flag with a warning icon for the user to spot dominance / silence.
  const ratioPct = Math.round(stats.talkRatio * 100);
  const healthy = ratioPct >= 15 && ratioPct <= 60;
  // Longest monologue >= 60s is the "watch this" threshold (Notta uses 90s).
  const longSpan = stats.longestMonologueMs / 1000;
  const longWarning = longSpan >= 60;

  return (
    <div className={`insights-speaker-card${expanded ? " expanded" : ""}`}>
      <div className="insights-speaker-head" onClick={() => setExpanded((v) => !v)}>
        <svg
          viewBox="0 0 24 24"
          width="12"
          height="12"
          className="caret"
          style={{ transform: expanded ? "rotate(0deg)" : "rotate(-90deg)" }}
          fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
        <span className="insights-avatar" style={{ background: color }}>{initial}</span>
        <span className="insights-speaker-name">{stats.speaker}</span>
        <span className="insights-speaker-time mono">{fmtTime(stats.totalMs)}</span>
      </div>

      {/* Timeline strip — where this speaker spoke. */}
      <div className="insights-timeline">
        {stats.turns.map((t, i) => {
          if (totalDurationMs <= 0) return null;
          const left = (t.startMs / totalDurationMs) * 100;
          const width = ((t.endMs - t.startMs) / totalDurationMs) * 100;
          return (
            <span
              key={i}
              className="insights-timeline-bar"
              style={{ left: `${left}%`, width: `${Math.max(0.3, width)}%`, background: color }}
              title={`${fmtTime(t.startMs)} – ${fmtTime(t.endMs)}`}
            />
          );
        })}
      </div>

      {expanded && (
        <div className="insights-speaker-body">
          {/* Gauge — talk ratio as a half-circle arc */}
          <Gauge value={stats.talkRatio} color={color} />

          <div className="insights-metrics">
            <div className="insights-metric">
              <div className="insights-metric-label">{t("insights.talkRatio")}</div>
              <div className="insights-metric-value">
                {ratioPct}%
                <span className={`insights-metric-status ${healthy ? "ok" : "warn"}`}>
                  {healthy ? "✓" : "⚠"}
                </span>
              </div>
            </div>
            <div className="insights-metric">
              <div className="insights-metric-label">{t("insights.monologues")}</div>
              <div className="insights-metric-value">{stats.monologues}</div>
            </div>
            <div className="insights-metric">
              <div className="insights-metric-label">{t("insights.longestMonologue")}</div>
              <div className="insights-metric-value">
                {fmtTime(stats.longestMonologueMs)}
                <span className={`insights-metric-status ${longWarning ? "warn" : "ok"}`}>
                  {longWarning ? "⚠" : "✓"}
                </span>
              </div>
            </div>
            <div className="insights-metric">
              <div className="insights-metric-label">{t("insights.pace")}</div>
              <div className="insights-metric-value">
                {stats.wpm != null ? (
                  <>{stats.wpm} <span className="muted small">{t("insights.wpm")}</span></>
                ) : (
                  <span className="muted small">—</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Half-circle gauge for talk ratio ──────────────────────────────
function Gauge({ value, color }: { value: number; color: string }) {
  // SVG half-circle: radius 50, center (60,60). value=0 → empty, 1 → full.
  const pctClamped = Math.max(0, Math.min(1, value));
  const r = 50;
  const circ = Math.PI * r; // half-circle circumference
  const dash = circ * pctClamped;
  return (
    <div className="insights-gauge">
      <svg viewBox="0 0 120 70" width="120" height="70">
        <path
          d="M 10 60 A 50 50 0 0 1 110 60"
          fill="none"
          stroke="var(--bg-2)"
          strokeWidth="9"
          strokeLinecap="round"
        />
        <path
          d="M 10 60 A 50 50 0 0 1 110 60"
          fill="none"
          stroke={color}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circ}`}
        />
      </svg>
      <div className="insights-gauge-label">
        <div className="insights-gauge-pct">{Math.round(pctClamped * 100)}%</div>
      </div>
    </div>
  );
}

function EmptyMsg({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="insights-empty">
      <div className="insights-empty-title">{title}</div>
      <div className="insights-empty-sub muted small">{sub}</div>
    </div>
  );
}

// ─── Stats math ─────────────────────────────────────────────────────
function computeStats(
  segments: RawSegment[],
  totalDurationMs: number,
  clusterMapping: Record<string, string>,
): { perSpeaker: SpeakerStats[]; speakingMs: number; silenceMs: number } {
  // Helper: convert raw cluster id (SPEAKER_NN) to the friendly label
  // the user already saw in the Clean view. Falls back to the raw
  // label when no mapping or mapping is "Unknown".
  function displayName(raw: string): string {
    const mapped = clusterMapping[raw];
    if (!mapped) return raw;
    const norm = mapped.trim();
    if (!norm || norm.toLowerCase() === "unknown") return raw;
    return norm;
  }
  // Aggregate by speaker.
  type Bucket = {
    totalMs: number;
    wordCount: number;
    turns: Array<{ startMs: number; endMs: number }>;
    monologueSpans: number[]; // ms of each "consecutive run" of turns
  };
  const map = new Map<string, Bucket>();
  let lastSpeaker = "";
  let currentRunMs = 0;
  let speakingMs = 0;

  // Sort by start_ms just in case backend returned out of order.
  const sorted = [...segments].sort((a, b) => (a.start_ms || 0) - (b.start_ms || 0));

  for (const seg of sorted) {
    if (seg.start_ms == null || seg.end_ms == null) continue;
    const dur = Math.max(0, seg.end_ms - seg.start_ms);
    speakingMs += dur;
    const rawSpk = (seg.speaker || "?").trim() || "?";
    // Translate SPEAKER_NN → friendly name via cluster_mapping. Two
    // segments from the same cluster (e.g. SPEAKER_01) collapse into
    // one card labelled with the user's chosen name (e.g. "Đại").
    const spk = displayName(rawSpk);
    if (!map.has(spk)) {
      map.set(spk, { totalMs: 0, wordCount: 0, turns: [], monologueSpans: [] });
    }
    const b = map.get(spk)!;
    b.totalMs += dur;
    b.turns.push({ startMs: seg.start_ms, endMs: seg.end_ms });
    // Word count: prefer word-level; fall back to whitespace split.
    if (seg.words && seg.words.length) b.wordCount += seg.words.length;
    else b.wordCount += (seg.text || "").trim().split(/\s+/).filter(Boolean).length;

    // Monologue run tracking — a "run" is consecutive segments by the
    // same speaker. When speaker changes, close the previous run.
    if (spk === lastSpeaker) {
      currentRunMs += dur;
    } else {
      if (lastSpeaker && currentRunMs > 0) {
        map.get(lastSpeaker)!.monologueSpans.push(currentRunMs);
      }
      currentRunMs = dur;
      lastSpeaker = spk;
    }
  }
  // Flush the trailing run.
  if (lastSpeaker && currentRunMs > 0) {
    map.get(lastSpeaker)!.monologueSpans.push(currentRunMs);
  }

  // Convert to SpeakerStats array, sorted by totalMs desc.
  const perSpeaker: SpeakerStats[] = [];
  for (const [spk, b] of map.entries()) {
    const longest = b.monologueSpans.length ? Math.max(...b.monologueSpans) : 0;
    perSpeaker.push({
      speaker: spk,
      totalMs: b.totalMs,
      talkRatio: speakingMs > 0 ? b.totalMs / speakingMs : 0,
      monologues: b.monologueSpans.length,
      longestMonologueMs: longest,
      wpm: b.totalMs > 1000
        ? Math.round(b.wordCount / (b.totalMs / 60_000))
        : null,
      turns: b.turns,
    });
  }
  perSpeaker.sort((a, b) => b.totalMs - a.totalMs);

  const silenceMs = Math.max(0, totalDurationMs - speakingMs);
  return { perSpeaker, speakingMs, silenceMs };
}

// ─── Format helpers ────────────────────────────────────────────────
function fmtTime(ms: number): string {
  if (!ms || ms < 0) ms = 0;
  const totalSec = Math.round(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}min ${s}s`;
  return `${s}s`;
}

function pct(num: number, den: number): number {
  if (den <= 0) return 0;
  return Math.max(0, Math.min(100, (num / den) * 100));
}

// Must match NottaCleanView's hashColor exactly (same HUES table, same
// hash formula) — that way each speaker's avatar/gauge color in the
// Insights pane equals the chip color the user sees in the Clean view.
const HUES = [142, 200, 280, 30, 350, 165, 50, 320];
function hashColor(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return `hsl(${HUES[Math.abs(h) % HUES.length]}, 65%, 60%)`;
}
