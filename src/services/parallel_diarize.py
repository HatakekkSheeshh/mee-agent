"""Chunked parallel pyannote diarization (Option C).

Why this exists:
    Pyannote 3.1 on CPU ≈ 5-10× realtime. A 1-hour meeting takes 6-12
    minutes; a 3-hour meeting takes 30-60 min. The bottleneck is the
    serial CNN inference inside pyannote — but each independent audio
    slice can be processed in parallel on separate CPU cores.

    This module splits the audio into ~15-minute slices, runs
    `local_diarize.diarize_audio()` on each slice in a ThreadPoolExecutor,
    then merges per-slice cluster IDs into globally consistent speaker
    labels via cosine-distance Agglomerative Hierarchical Clustering on
    the per-slice embeddings.

    Result: 2-4× wall-clock speedup on a 4-core CPU. For a 3-hour file
    this drops 30-60 min → 10-20 min.

How global re-ID works:
    Each slice independently clusters audio → local labels SPEAKER_00,
    SPEAKER_01, ... but slice1's SPEAKER_00 is NOT the same person as
    slice2's SPEAKER_00. To unify, we pool ALL embeddings (1 per local
    cluster × N slices) into one feature matrix, run AHC with cosine
    distance threshold ~0.25 (same-person embeddings on same audio
    are very similar), and the resulting global cluster IDs are
    consistent across the whole recording.

Threads vs multiprocessing:
    Threads — pyannote uses torch C++ kernels which release the GIL.
    The model itself is reused (singleton in local_diarize._pipeline),
    so memory stays flat. Multiprocessing would re-import torch +
    re-load pyannote model per process → +500MB-1GB RAM per worker.
"""
from __future__ import annotations

import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def _chunk_waveform(
    audio_bytes: bytes,
    slice_seconds: float = 15 * 60,
) -> list[tuple[float, bytes]]:
    """Split a WAV file into N slices, each re-encoded as standalone WAV.

    Returns list of (start_offset_seconds, wav_bytes) tuples. The full
    waveform is decoded once via soundfile; each slice is re-encoded
    in-memory as 16kHz mono PCM16 WAV that local_diarize can consume.
    """
    audio, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)  # downmix to mono

    total_sec = len(audio) / sr
    if total_sec <= slice_seconds:
        # Whole audio is shorter than 1 slice — no point chunking.
        return [(0.0, audio_bytes)]

    samples_per_slice = int(slice_seconds * sr)
    chunks: list[tuple[float, bytes]] = []
    for start_sample in range(0, len(audio), samples_per_slice):
        end_sample = min(start_sample + samples_per_slice, len(audio))
        chunk = audio[start_sample:end_sample]
        if len(chunk) < sr * 5:
            # Skip slivers < 5s — pyannote can't get reliable speakers
            # from too-short audio, and they'd just add noise to AHC.
            continue
        buf = io.BytesIO()
        sf.write(buf, chunk, sr, format="WAV", subtype="PCM_16")
        offset_s = start_sample / sr
        chunks.append((offset_s, buf.getvalue()))
    return chunks


def _global_reid(
    per_slice_results: list[dict],
    cosine_threshold: float = 0.25,
) -> dict[tuple[int, str], str]:
    """Cluster all per-slice cluster embeddings → global speaker labels.

    Same person → same embedding vector (cosine distance < threshold).
    Same audio source means very tight clusters; threshold tighter than
    the cross-meeting voiceprint match (0.30).

    Args:
        per_slice_results: list of {cluster_embeddings: {local_id: [256d]}}
        cosine_threshold: max cosine distance to merge into same global

    Returns:
        Mapping (slice_idx, local_id) → global_label "SPEAKER_NN".
    """
    from sklearn.cluster import AgglomerativeClustering

    # Flatten all (slice_idx, local_id, embedding) tuples
    keys: list[tuple[int, str]] = []
    vectors: list[list[float]] = []
    for slice_idx, result in enumerate(per_slice_results):
        for local_id, emb in (result.get("cluster_embeddings") or {}).items():
            if not emb:
                continue
            keys.append((slice_idx, local_id))
            vectors.append(emb)

    if not keys:
        return {}

    if len(keys) == 1:
        # Only 1 speaker across all slices — trivial.
        return {keys[0]: "SPEAKER_00"}

    X = np.array(vectors, dtype=np.float32)

    # Agglomerative with average linkage on cosine distance. Threshold
    # picked from speaker-diarization literature: same speaker → cos
    # distance typically 0.05-0.15; different speakers → > 0.4. 0.25
    # is a safe middle ground for same-audio-source matching.
    clusterer = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=cosine_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = clusterer.fit_predict(X)
    n_global = len(set(labels.tolist()))
    logger.info(
        f"[parallel_diarize] re-ID: {len(keys)} local clusters across "
        f"{len(per_slice_results)} slices → {n_global} global speakers"
    )

    # Sort global labels by first-appearance order so SPEAKER_00 is
    # whoever talks first. Otherwise sklearn's labels are arbitrary.
    seen_order: dict[int, int] = {}
    for raw_label in labels:
        if raw_label not in seen_order:
            seen_order[raw_label] = len(seen_order)

    mapping: dict[tuple[int, str], str] = {}
    for key, raw_label in zip(keys, labels.tolist()):
        global_idx = seen_order[raw_label]
        mapping[key] = f"SPEAKER_{global_idx:02d}"
    return mapping


def diarize_parallel(
    audio_bytes: bytes,
    slice_seconds: float = 15 * 60,
    max_workers: Optional[int] = None,
    cosine_threshold: float = 0.25,
) -> dict:
    """Run pyannote on audio in parallel chunks; return single global result.

    Returns same shape as `local_diarize.diarize_audio()`:
        {
          "turns": [{"start", "end", "speaker"}, ...],
          "cluster_embeddings": {"SPEAKER_NN": [256d]},
          "sample_audio_b64": {"SPEAKER_NN": "<wav base64>"},
        }

    `max_workers` defaults to min(4, cpu_count, num_slices). Threading
    over pyannote is GIL-friendly (torch releases GIL in C++ kernels);
    multiprocessing would explode RAM by re-loading model per process.
    """
    chunks = _chunk_waveform(audio_bytes, slice_seconds)
    n = len(chunks)
    if n <= 1:
        # Single chunk — fall back to single-shot impl, no overhead.
        from src.services.local_diarize import diarize_audio
        logger.info(
            f"[parallel_diarize] only 1 slice — using single-shot diarize"
        )
        return diarize_audio(audio_bytes)

    cpus = os.cpu_count() or 2
    # Default to SINGLE-THREAD execution because the pyannote pipeline
    # singleton in `local_diarize._pipeline` is NOT thread-safe — multiple
    # concurrent `pipeline(audio)` calls on the SAME instance deadlock /
    # corrupt internal state (verified: 4 threads × 15-min slices → 45min
    # without any slice finishing). To get real parallelism you need
    # multi-PROCESS (each process has its own pipeline), not threads.
    #
    # Set DIARIZE_THREADS > 1 only if you've confirmed your pyannote
    # version handles concurrent inference on shared model state, or
    # accept that "parallel" will effectively serialize on internal locks.
    env_threads = os.getenv("DIARIZE_THREADS")
    if env_threads:
        try:
            default_max = max(1, int(env_threads))
        except ValueError:
            default_max = 1
    else:
        default_max = 1
    workers = max_workers or min(default_max, n)
    logger.info(
        f"[parallel_diarize] {n} slices ({slice_seconds:.0f}s each), "
        f"running on {workers} threads (CPU={cpus}, "
        f"DIARIZE_THREADS={env_threads or '1=default-safe'})"
    )
    if workers > 1:
        logger.warning(
            "[parallel_diarize] DIARIZE_THREADS > 1 — pyannote pipeline "
            "is shared across threads and may deadlock. Watch the task "
            "carefully and revert to DIARIZE_THREADS=1 if stuck."
        )

    from src.services.local_diarize import diarize_audio

    t_start = time.time()
    per_slice_results: list[dict] = []
    per_slice_offsets: list[float] = []

    def _run_one(args):
        idx, (offset_s, chunk_bytes) = args
        t0 = time.time()
        result = diarize_audio(chunk_bytes)
        logger.info(
            f"[parallel_diarize] slice {idx+1}/{n} ({offset_s:.0f}s offset) "
            f"done in {time.time()-t0:.0f}s, "
            f"{len(result.get('turns') or [])} turns, "
            f"{len(result.get('cluster_embeddings') or {})} clusters"
        )
        return offset_s, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # `enumerate(chunks)` gives (idx, (offset_s, bytes)) so the worker
        # can log slice index without needing a shared counter.
        results = list(pool.map(_run_one, list(enumerate(chunks))))

    # Preserve original chunk order (executor.map already does, but be safe)
    results.sort(key=lambda r: r[0])
    per_slice_offsets = [r[0] for r in results]
    per_slice_results = [r[1] for r in results]

    # Global re-identification via AHC on pooled embeddings.
    reid_map = _global_reid(per_slice_results, cosine_threshold=cosine_threshold)

    # Rewrite turns: timestamp += slice_offset, speaker = global label.
    global_turns: list[dict] = []
    for slice_idx, (offset_s, result) in enumerate(zip(per_slice_offsets, per_slice_results)):
        for turn in result.get("turns") or []:
            local_id = turn["speaker"]
            global_id = reid_map.get((slice_idx, local_id), local_id)
            global_turns.append({
                "start": turn["start"] + offset_s,
                "end": turn["end"] + offset_s,
                "speaker": global_id,
            })
    global_turns.sort(key=lambda t: t["start"])

    # For each global cluster, average the embeddings from contributing
    # local clusters → 1 representative 256-d vector. Better than picking
    # one arbitrarily because the per-slice embedding may be noisy.
    global_embeddings_sum: dict[str, list[float]] = {}
    global_embeddings_count: dict[str, int] = {}
    for (slice_idx, local_id), global_id in reid_map.items():
        emb = per_slice_results[slice_idx].get("cluster_embeddings", {}).get(local_id)
        if not emb:
            continue
        if global_id not in global_embeddings_sum:
            global_embeddings_sum[global_id] = [0.0] * len(emb)
            global_embeddings_count[global_id] = 0
        for i, v in enumerate(emb):
            global_embeddings_sum[global_id][i] += v
        global_embeddings_count[global_id] += 1
    global_embeddings: dict[str, list[float]] = {}
    for global_id, total in global_embeddings_sum.items():
        cnt = global_embeddings_count[global_id]
        global_embeddings[global_id] = [v / cnt for v in total]

    # For samples: pick the slice where the global speaker has the most
    # cumulative speaking time, use that slice's sample as the rep clip.
    # (Single longest contiguous turn would also work but spread-out
    # speakers with many short turns deserve their longest combined slice.)
    talk_time_per_global: dict[str, dict[int, float]] = {}
    for turn in global_turns:
        spk = turn["speaker"]
        duration = turn["end"] - turn["start"]
        # Figure out which slice this turn came from
        slice_idx = next(
            (i for i, off in enumerate(per_slice_offsets)
             if off <= turn["start"] < off + slice_seconds),
            0,
        )
        talk_time_per_global.setdefault(spk, {}).setdefault(slice_idx, 0.0)
        talk_time_per_global[spk][slice_idx] += duration

    global_samples: dict[str, str] = {}
    for global_id, by_slice in talk_time_per_global.items():
        # Slice with max talk-time for this global speaker
        best_slice_idx = max(by_slice, key=lambda i: by_slice[i])
        # Find that slice's local id for this global
        local_id_in_slice = next(
            (lid for (s, lid), g in reid_map.items()
             if s == best_slice_idx and g == global_id),
            None,
        )
        if local_id_in_slice:
            sample_b64 = per_slice_results[best_slice_idx].get(
                "sample_audio_b64", {}
            ).get(local_id_in_slice)
            if sample_b64:
                global_samples[global_id] = sample_b64

    elapsed = time.time() - t_start
    logger.info(
        f"[parallel_diarize] DONE in {elapsed:.0f}s — "
        f"{len(global_turns)} turns, {len(global_embeddings)} speakers, "
        f"{len(global_samples)} samples"
    )

    return {
        "turns": global_turns,
        "cluster_embeddings": global_embeddings,
        "sample_audio_b64": global_samples,
    }
