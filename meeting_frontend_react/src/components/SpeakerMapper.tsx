// SpeakerMapper — panel above CleanEditor to confirm + save voice→name mappings.
//
// Sources for cluster list (priority):
//   1. clusterMapping prop (LLM cluster_mapping output) — primary source.
//      Pre-fills input with LLM's inferred name.
//   2. Fallback: scan segments for "SPEAKER_NN" labels (legacy path when
//      cleaner didn't return cluster_mapping).
//
// preMappedClusters: cluster ids that came from voiceprint DB cosine match.
// We mark these with "✓ Đã nhận diện" — no Save button (already in DB).
// Clusters without embedding (audio gone) get a disabled "⚠ Không nhận diện
// được" badge — text rename for those still happens via the inline dropdown
// in CleanEditor ("+ Tên khác…"), not through this panel.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { useApp } from "../store/AppContext";

interface Props {
  recordingId: string;
  segments: { speaker?: string; text: string }[];
  /** LLM-inferred cluster_id → name from cleaner. */
  clusterMapping?: Record<string, string>;
  /** Clusters auto-matched from voiceprint DB. These are "verified". */
  preMappedClusters?: string[];
  /** Clusters that have stored embeddings — Save button only works for these.
   * If missing, recording was uploaded before Phase 2 or audio was too short. */
  availableClusters?: string[];
  /** Clusters that have a stored 3s WAV sample on disk. Used to decide
   * whether to render the ▶ play button per row. Comes from
   * recording.speaker_samples (set by /diarize-result + /import-transcript
   * when sample_audio_b64 is present). */
  availableSamples?: string[];
  /** Called after a successful save — parent should refetch /clean to pick
   * up the renamed segments + cluster_mapping. */
  onSaved?: () => void;
}

interface Row {
  clusterId: string;
  inferredName: string;
  preMatched: boolean;
  hasEmbedding: boolean;
  hasSample: boolean;
}

export function SpeakerMapper({
  recordingId,
  segments,
  clusterMapping,
  preMappedClusters,
  availableClusters,
  availableSamples,
  onSaved,
}: Props) {
  const { t } = useApp();
  const rows = useMemo<Row[]>(() => {
    const preSet = new Set(preMappedClusters || []);
    // Distinguish "backend didn't tell us" (undefined → assume available, let
    // bind fail with a 400 if not) from "backend told us — there are none"
    // (empty array → show ⚠ on every cluster). Without this check, legacy
    // recordings uploaded before Phase 2 silently let the Save button through
    // and the user gets the cryptic "No embedding stored" alert.
    const availProvided = availableClusters !== undefined;
    const availSet = new Set(availableClusters || []);
    const hasEmb = (cid: string) => !availProvided || availSet.has(cid);
    const sampleSet = new Set(availableSamples || []);
    const hasSample = (cid: string) => sampleSet.has(cid);
    // Primary: use cluster_mapping keys (LLM gave us full picture)
    if (clusterMapping && Object.keys(clusterMapping).length > 0) {
      return Object.entries(clusterMapping)
        .filter(([cid]) => /^SPEAKER_\d+$/.test(cid))
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([clusterId, name]) => ({
          clusterId,
          inferredName: name === "Unknown" ? "" : name,
          preMatched: preSet.has(clusterId),
          hasEmbedding: hasEmb(clusterId),
          hasSample: hasSample(clusterId),
        }));
    }
    // Fallback: extract from segments
    const ids = new Set<string>();
    for (const s of segments) {
      if (s.speaker && /^SPEAKER_\d+$/.test(s.speaker)) ids.add(s.speaker);
    }
    return [...ids].sort().map((cid) => ({
      clusterId: cid,
      inferredName: "",
      preMatched: preSet.has(cid),
      hasEmbedding: hasEmb(cid),
      hasSample: hasSample(cid),
    }));
  }, [clusterMapping, preMappedClusters, availableClusters, availableSamples, segments]);

  const [names, setNames] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Reset state on recording switch or new mapping load
  useEffect(() => {
    const initial: Record<string, string> = {};
    for (const r of rows) {
      if (r.inferredName) initial[r.clusterId] = r.inferredName;
    }
    setNames(initial);
    setSavingId(null);
    setSaved(new Set());
    // Stop any previewing clip when the recording changes — the URL points
    // to a different recording's audio after the switch.
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.removeAttribute("src");
    }
    setPlayingId(null);
  }, [recordingId, rows]);

  function togglePlay(clusterId: string) {
    const a = audioRef.current;
    if (!a) return;
    // Re-clicking the currently playing row pauses it.
    if (playingId === clusterId && !a.paused) {
      a.pause();
      setPlayingId(null);
      return;
    }
    a.src = api.speakerSampleUrl(recordingId, clusterId);
    setPlayingId(clusterId);
    a.play().catch(() => setPlayingId(null));
  }

  async function handleSave(clusterId: string) {
    const name = (names[clusterId] || "").trim();
    if (!name) return;
    setSavingId(clusterId);
    try {
      await api.voiceprints.bind(recordingId, clusterId, name);
      setSaved((prev) => new Set(prev).add(clusterId));
      // Backend also renamed in clean_segments → ask parent to refetch
      // so editor + cluster_mapping refresh with new name.
      onSaved?.();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : (e as Error).message;
      alert(t("speakerMapper.saveError", { msg }));
    } finally {
      setSavingId(null);
    }
  }

  // Always render so user can still "+ Thêm speaker" even when pyannote
  // returned zero clusters (e.g. recording with no diarization at all).

  return (
    <div
      style={{
        background: "var(--surface-2)",
        border: "1px solid var(--border-2)",
        borderRadius: "var(--r-sm)",
        padding: "10px 12px",
        marginBottom: 8,
        fontSize: 13,
      }}
    >
      <div style={{ marginBottom: 8, color: "var(--text-mute)", fontSize: 12 }}>
        {t("speakerMapper.title")}
      </div>
      {rows.length === 0 && (
        <div
          style={{
            padding: "8px 10px",
            background: "var(--surface-3)",
            borderRadius: 4,
            fontSize: 12,
            color: "var(--text-mute)",
          }}
        >
          {t("speakerMapper.emptyPyannote")}
        </div>
      )}
      <div style={{ display: "grid", gap: 6 }}>
        {rows.map((r) => {
          const isSaved = saved.has(r.clusterId);
          const isSaving = savingId === r.clusterId;
          const verified = r.preMatched || isSaved;
          return (
            <div
              key={r.clusterId}
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <code
                style={{
                  background: "var(--surface-3)",
                  padding: "2px 8px",
                  borderRadius: 4,
                  fontSize: 12,
                  minWidth: 100,
                  textAlign: "center",
                }}
              >
                {r.clusterId}
              </code>
              {r.hasSample && (
                <button
                  type="button"
                  onClick={() => togglePlay(r.clusterId)}
                  title={t("speakerMapper.playTitle")}
                  aria-label={t("speakerMapper.playTitle")}
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: "50%",
                    border: "1px solid var(--border-2)",
                    background: "var(--surface)",
                    color: "var(--accent-deep)",
                    fontSize: 11,
                    lineHeight: 1,
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {playingId === r.clusterId ? "❚❚" : "▶"}
                </button>
              )}
              <span style={{ color: "var(--text-mute)" }}>→</span>
              <input
                type="text"
                className="field"
                placeholder={t("speakerMapper.namePlaceholder")}
                value={names[r.clusterId] || ""}
                onChange={(e) =>
                  setNames((prev) => ({ ...prev, [r.clusterId]: e.target.value }))
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSave(r.clusterId);
                }}
                disabled={isSaving || verified}
                style={{ flex: 1, height: 28, padding: "2px 8px", fontSize: 13 }}
              />
              {r.preMatched ? (
                <span
                  style={{ color: "var(--accent)", fontSize: 12, padding: "0 8px" }}
                  title={t("speakerMapper.recognizedTitle")}
                >
                  {t("speakerMapper.recognized")}
                </span>
              ) : isSaved ? (
                <span
                  style={{ color: "var(--accent)", fontSize: 12, padding: "0 8px" }}
                >
                  {t("speakerMapper.saved")}
                </span>
              ) : !r.hasEmbedding ? (
                <span
                  style={{
                    color: "var(--text-mute)",
                    fontSize: 12,
                    padding: "0 8px",
                  }}
                  title={t("speakerMapper.noEmbeddingTitle")}
                >
                  {t("speakerMapper.noEmbedding")}
                </span>
              ) : (
                <button
                  className="btn btn-primary btn-xs"
                  type="button"
                  onClick={() => handleSave(r.clusterId)}
                  disabled={!names[r.clusterId]?.trim() || isSaving}
                  title={t("speakerMapper.saveTitle")}
                >
                  {isSaving ? "..." : t("speakerMapper.save")}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {/* Shared playback element — one per panel, swap src on play. */}
      <audio
        ref={audioRef}
        onEnded={() => setPlayingId(null)}
        onPause={() => setPlayingId((prev) => (audioRef.current?.paused ? null : prev))}
        style={{ display: "none" }}
      />
    </div>
  );
}
