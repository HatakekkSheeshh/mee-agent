"""Local pyannote diarization — fallback when PhoWhisper server is down.

When MaaS Whisper is used (text-only response, no speaker tags), this module
runs pyannote 3.1 locally on the audio file to recover:
  - Speaker turns (start, end, SPEAKER_NN)
  - Per-cluster 256-d embeddings (for voiceprint matching)

Then we proportionally split MaaS Whisper's plain text across the turns to
produce structured segments with {start, end, speaker, text}.

Caveat — proportional split is approximate (loses ±0.5-2s at sentence
boundaries) because MaaS Whisper doesn't expose word-level timestamps.

Setup:
  pip install pyannote.audio torch torchaudio
  export HF_TOKEN=hf_xxx                    # HuggingFace token
  # Accept terms at:
  #   https://huggingface.co/pyannote/speaker-diarization-3.1
  #   https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM

Model loading is LAZY (first call only) so startup stays fast.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import warnings
from typing import Optional

# Silence noisy pyannote/torchcodec warnings — they don't affect us:
#   - torchcodec: we bypass it by pre-loading audio with soundfile
#   - pyannote std() numerical: harmless internal calculation on short frames
warnings.filterwarnings(
    "ignore", message=".*torchcodec is not installed.*"
)
warnings.filterwarnings(
    "ignore", message=".*degrees of freedom is <= 0.*"
)

logger = logging.getLogger(__name__)

# Lazy-loaded singletons (heavy ~5s init each).
_pipeline = None
_embedder = None
_load_lock = threading.Lock()


def _ensure_loaded() -> tuple[object, object]:
    """First-call init of pyannote pipeline + embedder. Cached forever."""
    global _pipeline, _embedder
    if _pipeline is not None and _embedder is not None:
        return _pipeline, _embedder
    with _load_lock:
        if _pipeline is not None and _embedder is not None:
            return _pipeline, _embedder
        token = os.getenv("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "HF_TOKEN env var required for pyannote. Add to .env: "
                "HF_TOKEN=hf_xxxxxxxxxxxxx"
            )
        # Import here so module loads even when pyannote not installed.
        from pyannote.audio import Pipeline, Inference, Model
        logger.info("[local_diarize] loading pyannote pipeline (first call)…")
        # pyannote 4.x: Pipeline.from_pretrained uses `token`; Inference
        # doesn't accept token directly — load Model first then wrap.
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", token=token,
        )
        _emb_model = Model.from_pretrained(
            "pyannote/wespeaker-voxceleb-resnet34-LM", token=token,
        )
        _embedder = Inference(_emb_model, window="whole")
        logger.info("[local_diarize] pyannote loaded ✓")
    return _pipeline, _embedder


def diarize_audio(
    audio_bytes: bytes, sample_rate: Optional[int] = None
) -> dict:
    """Run pyannote on raw audio bytes.

    Args:
        audio_bytes: WAV/MP3/FLAC bytes
        sample_rate: hint, not required (pyannote re-samples internally)

    Returns:
        {
          "turns": [{"start": float, "end": float, "speaker": "SPEAKER_00"}, ...],
          "cluster_embeddings": {"SPEAKER_00": [...256...], "SPEAKER_01": [...]}
        }
    Returns {"turns": [], "cluster_embeddings": {}} on any error (logged).
    """
    try:
        pipeline, embedder = _ensure_loaded()
    except Exception as e:
        logger.warning(f"[local_diarize] cannot load pyannote: {e}")
        return {"turns": [], "cluster_embeddings": {}}

    # pyannote wants a file path. Write tempfile.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        # pyannote 4 normally decodes audio via torchcodec → needs system
        # ffmpeg lib at a very specific version (libavutil.so.56/57/59/60).
        # Bypass by pre-loading audio with soundfile → torch tensor dict,
        # which pyannote accepts directly. Works with any FFmpeg version.
        import soundfile as sf
        import numpy as np
        import torch

        audio_np, sr = sf.read(tmp_path, dtype="float32")
        # pyannote wants (channels, time) shape.
        if audio_np.ndim == 1:
            audio_np = audio_np[np.newaxis, :]
        else:
            audio_np = audio_np.T
        waveform = torch.from_numpy(np.ascontiguousarray(audio_np))
        audio_input = {"waveform": waveform, "sample_rate": int(sr)}

        logger.info(
            f"[local_diarize] running pyannote diarization on "
            f"{waveform.shape[1] / sr:.1f}s audio…"
        )
        output = pipeline(audio_input)

        # pyannote 4: pipeline() returns DiarizeOutput. The Annotation
        # (with itertracks) lives at .speaker_diarization. Older pyannote 3
        # returns the Annotation directly — handle both.
        if hasattr(output, "speaker_diarization"):
            diarization = output.speaker_diarization
        else:
            diarization = output

        # Normalize speaker labels — pyannote 4 may return integers (0, 1, 2)
        # instead of "SPEAKER_00"/"SPEAKER_01" strings. Cleaner LLM + voiceprint
        # matching expect the "SPEAKER_NN" form everywhere.
        def _normalize(spk) -> str:
            s = str(spk).strip()
            if s.startswith("SPEAKER_"):
                return s
            # Integer or short int-like → zero-pad to SPEAKER_NN
            try:
                n = int(s)
                return f"SPEAKER_{n:02d}"
            except ValueError:
                return s  # leave non-numeric labels (rare) as-is

        turns: list[dict] = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            turns.append({
                "start": float(turn.start),
                "end": float(turn.end),
                "speaker": _normalize(speaker),
            })
        logger.info(f"[local_diarize] got {len(turns)} turns, "
                    f"{len(set(t['speaker'] for t in turns))} speakers")

        # Per-cluster embedding. pyannote 4 DiarizeOutput may already include
        # `.embeddings` dict {speaker: tensor} — use that if present to skip
        # an extra inference pass.
        cluster_embeddings: dict[str, list[float]] = {}
        builtin_embs = getattr(output, "embeddings", None)
        if builtin_embs:
            for spk, emb in builtin_embs.items():
                if hasattr(emb, "numpy"):
                    emb = emb.numpy()
                try:
                    cluster_embeddings[spk] = emb.flatten().tolist()
                except Exception as e:
                    logger.warning(
                        f"[local_diarize] built-in embedding {spk} bad shape: {e}"
                    )

        if not cluster_embeddings:
            # Fallback: compute manually from longest turn per speaker.
            from pyannote.core import Segment
            for spk in set(t["speaker"] for t in turns):
                spk_turns = [t for t in turns if t["speaker"] == spk]
                best = max(spk_turns, key=lambda t: t["end"] - t["start"])
                seg_start = best["start"]
                seg_end = min(best["end"], best["start"] + 10.0)
                try:
                    emb = embedder.crop(audio_input, Segment(seg_start, seg_end))
                    if hasattr(emb, "numpy"):
                        emb = emb.numpy()
                    cluster_embeddings[spk] = emb.flatten().tolist()
                except Exception as e:
                    logger.warning(
                        f"[local_diarize] embedding {spk} failed: {e}"
                    )

        return {
            "turns": turns,
            "cluster_embeddings": cluster_embeddings,
        }
    except Exception as e:
        logger.exception(f"[local_diarize] failed: {e}")
        return {"turns": [], "cluster_embeddings": {}}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def split_text_proportional(
    text: str,
    turns: list[dict],
) -> list[dict]:
    """Distribute plain text across speaker turns proportionally by duration.

    MaaS Whisper returns text-only. We don't have word timestamps to align
    precisely. Best effort: assume words are spoken at uniform rate, split
    text by char count proportional to each turn's duration.

    Returns: [{speaker, text, start, end}, ...] aligned to turns.
    """
    if not turns or not text.strip():
        return []
    total_dur = sum(max(0.0, t["end"] - t["start"]) for t in turns)
    if total_dur <= 0:
        return [{
            "speaker": turns[0]["speaker"],
            "text": text,
            "start": turns[0]["start"],
            "end": turns[-1]["end"],
        }]
    # Use words to split — sentences/punctuation may break unevenly but
    # word boundaries respect language structure better than char counts.
    words = text.split()
    if not words:
        return []
    out: list[dict] = []
    word_idx = 0
    total_words = len(words)
    for i, t in enumerate(turns):
        dur = max(0.0, t["end"] - t["start"])
        # Allocate words proportional to this turn's share of total duration.
        if i == len(turns) - 1:
            take = total_words - word_idx  # remainder
        else:
            take = max(1, round(dur / total_dur * total_words))
        chunk = " ".join(words[word_idx : word_idx + take]).strip()
        word_idx += take
        if chunk:
            out.append({
                "speaker": t["speaker"],
                "text": chunk,
                "start": t["start"],
                "end": t["end"],
            })
    return out
