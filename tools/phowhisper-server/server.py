"""PhoWhisper-large + pyannote 3.1 server — OpenAI-compatible STT with diarization.

Endpoint:
    POST /v1/audio/transcriptions   (OpenAI-compatible)
    Body: multipart/form-data — `file` (audio), optional `language`, `prompt`,
                                `min_speakers`, `max_speakers`
    Response:
        {
          "text": "SPEAKER_00: Tuấn deploy v1...\nSPEAKER_01: OK chốt...",
          "language": "vi",
          "segments": [
            {"speaker": "SPEAKER_00", "text": "...", "start": 0.0, "end": 3.2},
            ...
          ]
        }

Run:
    export HF_TOKEN=hf_xxxxxxxxxxxxx
    python server.py
    # Server listens on port 9100 by default. Set PORT=9101 for live mode.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from typing import Optional

import torch
import torchaudio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pyannote.audio import Pipeline as DiarizePipeline
from transformers import pipeline as hf_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ───── Config ─────────────────────────────────────────────────────
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32
ASR_MODEL = os.getenv("ASR_MODEL", "vinai/PhoWhisper-large")  # or PhoWhisper-medium for live
DIARIZE_MODEL = os.getenv("DIARIZE_MODEL", "pyannote/speaker-diarization-3.1")
HF_TOKEN = os.getenv("HF_TOKEN")
PORT = int(os.getenv("PORT", "9100"))
SAMPLE_RATE = 16000

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN env var required for pyannote diarization")

logger.info(f"Loading models: device={DEVICE}, dtype={TORCH_DTYPE}")
logger.info(f"  ASR: {ASR_MODEL}")
logger.info(f"  Diarize: {DIARIZE_MODEL}")

# Load ASR once (HF transformers pipeline)
ASR = hf_pipeline(
    "automatic-speech-recognition",
    model=ASR_MODEL,
    torch_dtype=TORCH_DTYPE,
    device=DEVICE,
    chunk_length_s=30,           # PhoWhisper trained on 30s chunks
    return_timestamps=True,      # need segment-level timestamps for diarization align
)
logger.info("ASR loaded.")

# Load diarization (pyannote >=3.3 renamed use_auth_token → token)
DIARIZE = DiarizePipeline.from_pretrained(DIARIZE_MODEL, token=HF_TOKEN)
if DEVICE == "cuda":
    DIARIZE.to(torch.device("cuda"))
logger.info("Diarize loaded.")

app = FastAPI(title="PhoWhisper + pyannote server")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "asr_model": ASR_MODEL,
        "diarize_model": DIARIZE_MODEL,
        "port": PORT,
    }


def _load_audio(path: str) -> tuple[torch.Tensor, int]:
    """Load any audio to mono 16kHz tensor — uses torchaudio (handles mp3/wav/m4a via ffmpeg backend)."""
    waveform, sr = torchaudio.load(path)
    # to mono
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    # resample to 16kHz
    if sr != SAMPLE_RATE:
        resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=SAMPLE_RATE)
        waveform = resampler(waveform)
    return waveform, SAMPLE_RATE


def _merge_diarize_turns(
    diarize_segs: list[dict],
    min_dur: float = 0.4,
    max_gap: float = 0.5,
) -> list[dict]:
    """Clean up pyannote turns for stable per-turn ASR.

    Steps:
    1. Sort by start time.
    2. Resolve overlapping turns: if two turns overlap, give each speaker only
       the non-overlapping portion (the overlap region is ambiguous cross-talk
       → drop it).
    3. Drop turns shorter than `min_dur` seconds (likely noise).
    4. Merge consecutive same-speaker turns whose gap is < `max_gap` seconds.
    """
    if not diarize_segs:
        return []

    # 1. Sort
    segs = sorted(diarize_segs, key=lambda d: (d["start"], d["end"]))

    # 2. Resolve overlaps: keep each turn but cap end at next-different-speaker's start
    resolved = []
    for i, d in enumerate(segs):
        s, e, spk = d["start"], d["end"], d["speaker"]
        # If next turn starts before this ends AND is a different speaker → cap at that boundary
        for j in range(i + 1, len(segs)):
            nxt = segs[j]
            if nxt["start"] >= e:
                break
            if nxt["speaker"] != spk:
                e = min(e, nxt["start"])
        # Skip if we've collapsed below threshold
        if e - s < min_dur:
            continue
        resolved.append({"speaker": spk, "start": s, "end": e})

    # 3. Merge consecutive same-speaker turns with small gaps
    merged = []
    for r in resolved:
        if merged and merged[-1]["speaker"] == r["speaker"] and (r["start"] - merged[-1]["end"]) <= max_gap:
            merged[-1]["end"] = r["end"]
        else:
            merged.append(dict(r))

    return merged


def _word_speaker(word_ts: tuple, turns: list[dict]) -> str:
    """Find which speaker turn contains this word's midpoint."""
    s, e = word_ts if word_ts else (None, None)
    if s is None and e is None:
        return "SPEAKER_UNKNOWN"
    # Use midpoint (or whichever end exists)
    pt = (s + e) / 2 if (s is not None and e is not None) else (s if s is not None else e)
    # Containing turn
    for t in turns:
        if t["start"] <= pt <= t["end"]:
            return t["speaker"]
    # Nearest turn by midpoint distance
    if not turns:
        return "SPEAKER_UNKNOWN"
    return min(turns, key=lambda t: min(abs(t["start"] - pt), abs(t["end"] - pt)))["speaker"]


def _align_words_to_turns(
    word_chunks: list[dict],
    turns: list[dict],
    full_text_fallback: str,
    total_dur: float,
) -> tuple[list[dict], list[str]]:
    """Group consecutive same-speaker words into segments.

    word_chunks: HF Whisper word-level output, each {text, timestamp: (s, e)}.
    turns: pyannote turns (already merged + cleaned).
    """
    # Fallback: no word chunks usable → return full_text under best-guess speaker
    if not word_chunks or all(not c.get("timestamp") for c in word_chunks):
        if not full_text_fallback:
            return [], []
        spk = turns[0]["speaker"] if turns else "SPEAKER_UNKNOWN"
        return (
            [{"speaker": spk, "text": full_text_fallback, "start": 0.0, "end": total_dur}],
            [f"{spk}: {full_text_fallback}"],
        )

    if not turns:
        # No diarization → 1 segment for everything
        return (
            [{"speaker": "SPEAKER_UNKNOWN", "text": full_text_fallback,
              "start": 0.0, "end": total_dur}],
            [full_text_fallback],
        )

    segments_out, text_lines = [], []
    cur_spk = None
    cur_words: list[str] = []
    cur_start: float | None = None
    cur_end: float | None = None

    def _flush():
        if cur_spk and cur_words:
            text = " ".join(cur_words).strip()
            if text:
                segments_out.append({
                    "speaker": cur_spk,
                    "text": text,
                    "start": cur_start or 0.0,
                    "end": cur_end or 0.0,
                })
                text_lines.append(f"{cur_spk}: {text}")

    for c in word_chunks:
        word = (c.get("text") or "").strip()
        if not word:
            continue
        ts = c.get("timestamp") or (None, None)
        spk = _word_speaker(ts, turns)
        s, e = ts if ts else (None, None)

        if spk != cur_spk:
            _flush()
            cur_spk = spk
            cur_words = [word]
            cur_start = s if s is not None else cur_end or 0.0
            cur_end = e if e is not None else cur_start
        else:
            cur_words.append(word)
            if e is not None:
                cur_end = e

    _flush()
    return segments_out, text_lines


def _assign_speaker(asr_segment: dict, diarize_segments: list[dict]) -> str:
    """For an ASR segment, return the pyannote speaker with most overlap.

    Robust to Whisper returning None for end-timestamp (happens on last chunk
    or when audio is cut mid-word) and to zero-overlap cases. Falls back to
    start-of-chunk → containing turn → nearest turn by midpoint.
    """
    if not diarize_segments:
        return "SPEAKER_UNKNOWN"

    s, e = asr_segment.get("timestamp", (None, None))

    if s is None and e is None:
        return "SPEAKER_UNKNOWN"

    # End missing → use start as point-match
    if e is None:
        for d in diarize_segments:
            if d["start"] <= s <= d["end"]:
                return d["speaker"]
        return min(diarize_segments, key=lambda d: abs(d["start"] - s))["speaker"]

    # Start missing → use end as point-match
    if s is None:
        for d in diarize_segments:
            if d["start"] <= e <= d["end"]:
                return d["speaker"]
        return min(diarize_segments, key=lambda d: abs(d["end"] - e))["speaker"]

    # Both present → max overlap
    best_spk, best_overlap = None, 0.0
    for d in diarize_segments:
        overlap = max(0.0, min(e, d["end"]) - max(s, d["start"]))
        if overlap > best_overlap:
            best_overlap = overlap
            best_spk = d["speaker"]

    if best_spk is not None:
        return best_spk

    # No overlap (gap between ASR + diarize boundaries) → nearest by midpoint
    mid = (s + e) / 2
    for d in diarize_segments:
        if d["start"] <= mid <= d["end"]:
            return d["speaker"]
    return min(
        diarize_segments,
        key=lambda d: abs((d["start"] + d["end"]) / 2 - mid),
    )["speaker"]


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default="vi"),
    prompt: Optional[str] = Form(default=None),    # initial_prompt for code-switching hint
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
    model: Optional[str] = Form(default=None),     # ignored — server picks
    response_format: Optional[str] = Form(default="json"),
):
    """OpenAI-compatible /v1/audio/transcriptions + speaker diarization."""
    audio_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        # 1. Load audio → 16kHz mono tensor
        waveform, sr = _load_audio(tmp_path)
        audio_np = waveform.squeeze(0).numpy()

        # Build ASR generate_kwargs (shared between full + per-turn calls)
        generate_kwargs = {
            "language": language or "vi",
            "task": "transcribe",
        }
        if prompt:
            try:
                prompt_ids = ASR.tokenizer.get_prompt_ids(prompt, return_tensors="pt")
                if DEVICE == "cuda":
                    prompt_ids = prompt_ids.to("cuda")
                generate_kwargs["prompt_ids"] = prompt_ids
            except Exception as e:
                logger.warning(f"prompt encoding failed, skipping prompt: {e}")

        # 2. Diarization — pyannote
        diarize_input = {"waveform": waveform, "sample_rate": sr}
        d_kwargs = {}
        if min_speakers is not None: d_kwargs["min_speakers"] = min_speakers
        if max_speakers is not None: d_kwargs["max_speakers"] = max_speakers
        annotation = DIARIZE(diarize_input, **d_kwargs)

        # pyannote 3.3+ wraps in DiarizeOutput; unwrap to Annotation
        if hasattr(annotation, "speaker_diarization"):
            annotation = annotation.speaker_diarization
        elif hasattr(annotation, "diarization"):
            annotation = annotation.diarization

        diarize_segs = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            diarize_segs.append({
                "speaker": speaker,
                "start": float(turn.start),
                "end": float(turn.end),
            })

        unique_spks = sorted({d["speaker"] for d in diarize_segs})
        logger.info(
            f"Diarize: {len(diarize_segs)} turns, {len(unique_spks)} unique speakers={unique_spks}"
        )

        # 3. Merge consecutive same-speaker turns (gap < 0.5s) + drop overlapping
        # short turns (< 0.4s — usually cross-talk noise). This gives stable chunks
        # to run ASR on per-speaker.
        merged_turns = _merge_diarize_turns(diarize_segs, min_dur=0.4, max_gap=0.5)
        logger.info(f"Merged into {len(merged_turns)} non-overlapping turns")
        for t in merged_turns[:10]:
            logger.info(f"  {t['speaker']}: {t['start']:.2f}–{t['end']:.2f}s")

        # 4. Single-pass ASR with WORD-LEVEL timestamps. Word timestamps come from
        # cross-attention weights — they don't need the model to predict timestamp
        # tokens, so they work even when segment-level timestamps fail (which is
        # PhoWhisper's bug). 1 ASR forward pass, no per-turn loop.
        asr_result = ASR(
            audio_np,
            generate_kwargs=generate_kwargs,
            return_timestamps="word",
        )
        full_text = (asr_result.get("text") or "").strip()
        word_chunks = asr_result.get("chunks", []) or []
        logger.info(
            f"ASR: {len(word_chunks)} word-chunks. Samples: "
            f"{[(c.get('text'), c.get('timestamp')) for c in word_chunks[:5]]}"
        )

        # 5. Assign each word to its pyannote turn (point-in-segment match) then
        # group consecutive same-speaker words into output segments.
        segments_out, text_lines = _align_words_to_turns(
            word_chunks, merged_turns, full_text, total_dur=float(len(audio_np)) / sr,
        )

        return JSONResponse({
            "text": "\n".join(text_lines),
            "language": language or "vi",
            "segments": segments_out,
        })

    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try: os.unlink(tmp_path)
        except Exception: pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
