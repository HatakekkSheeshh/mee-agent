"""faster-whisper large-v3 + pyannote 3.1 — Kaggle GPU notebook.

This is the WORD-ACCURATE STT backend for Mee. Same shape as phowhisper_server.py
(OpenAI /v1/audio/transcriptions + diarization + cluster embeddings) but:

  • Backend: CTranslate2 (faster_whisper) instead of HF transformers pipeline.
    Whisper-large-v3 at int8 ≈ 3GB VRAM vs ~13GB fp16 → no T4 OOM.
  • Word-level timestamps: returned natively via DTW from attention. No
    cross_attentions explosion that killed the HF path on long chunks.
  • Throughput: 4-7× faster than HF pipeline on the same audio (CTranslate2
    is C++ kernels + INT8 + batched).

The /v1/audio/transcriptions response is augmented: each segment now carries
`words[]` with absolute `start`/`end` seconds — the FE Notta view uses these
to highlight the playing word exactly (not the approximate even-distribute
fallback the current pipeline relies on).

Copy each "CELL N" block into a separate Kaggle notebook cell and run in
order. After Cell 4 prints the public URL, set it in your project .env:
    FASTER_WHISPER_BASE_URL=https://xxxx.trycloudflare.com
    FASTER_WHISPER_API_KEY=    # leave empty unless you add auth

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP (one-time):
  1. Kaggle → New Notebook → Settings:
       • Accelerator = GPU T4 x2
       • Internet = ON
       • Persistence = Files only
  2. Add-ons → Secrets → thêm:
       HF_TOKEN  = hf_xxxxxxx  (đã accept ToS pyannote/speaker-diarization-3.1
                   và pyannote/wespeaker-voxceleb-resnet34-LM)
  3. Paste cells 1→5, chạy lần lượt
  4. Cell 4 in URL → set vào .env: FASTER_WHISPER_BASE_URL=...
  5. Trong app, chọn "faster-whisper" trong STT dropdown của recording.
  6. Giữ tab Kaggle mở — cell 5 (keepalive) chống idle.

LƯU Ý:
  • Kaggle kernel tối đa 12h, idle kick ~9-30 phút (keepalive xử lý).
  • T4 x2 = 30GB tổng. faster-whisper-large int8 + pyannote ~5GB → dư
    rất nhiều, có thể tăng batch size sau này.
  • Lần đầu chạy cell 2 mất ~3-5 phút (download model). Sau đó cache.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ════════════════════════════════════════════════════════════════════
# CELL 1 — Install dependencies (~3 min lần đầu, cache sau đó)
# ════════════════════════════════════════════════════════════════════

# !pip install -q \
#     "faster-whisper>=1.0" \
#     "pyannote.audio==3.3.2" \
#     "soundfile" \
#     "fastapi" \
#     "uvicorn[standard]" \
#     "python-multipart" \
#     "huggingface_hub==0.26.5" \
#     "transformers==4.46.3" \
#     "torchmetrics==1.4.3"
#
# NOTE on this exact combo (Dec 2024 working set):
#  • huggingface_hub 0.26.x still exposes `is_offline_mode` (removed in 1.x)
#  • transformers 4.46.x has `LossKwargs` (added in 4.45) — pyannote → lightning
#    → torchmetrics → transformers picks it up
#  • torchmetrics 1.4.x is the last to work with this transformers pin
#
# CRITICAL: do NOT install torchaudio. Kaggle's image ships a torchaudio
# built against the bundled torch C++ ABI. Pip-upgrading torchaudio in
# isolation pulls a binary linked against a different torch → loading
# `_torchaudio.abi3.so` fails with OSError. Same logic applies to torch
# itself. Trust the base image's torch+torchaudio pair.
#
# After pip finishes, RESTART KERNEL (module cache holds the broken versions).


# ════════════════════════════════════════════════════════════════════
# CELL 2 — Load models lên GPU (~3-5 min lần đầu, ~30s sau đó)
# ════════════════════════════════════════════════════════════════════

import os
import logging
import torch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("faster_whisper")

# ── Secrets ──────────────────────────────────────────────────────
try:
    from kaggle_secrets import UserSecretsClient
    _secrets = UserSecretsClient()
    HF_TOKEN = _secrets.get_secret("HF_TOKEN")
except Exception:
    HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not HF_TOKEN:
    raise RuntimeError(
        "HF_TOKEN required. Thêm vào Kaggle Secrets (Add-ons → Secrets) "
        "hoặc set env var. Cần accept ToS tại:\n"
        "  https://huggingface.co/pyannote/speaker-diarization-3.1\n"
        "  https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM"
    )

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SAMPLE_RATE = 16000
MAX_EMBED_SECONDS = 10.0

print(f"CUDA: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

# ── 1. ASR: faster-whisper large-v3 ─────────────────────────────
# int8_float16 = INT8 weights + FP16 compute → ~3GB VRAM, ~5× speed vs HF.
# CTranslate2 handles word_timestamps via DTW from attention — no OOM.
from faster_whisper import WhisperModel

log.info("Loading faster-whisper large-v3...")
ASR = WhisperModel(
    "large-v3",
    device=DEVICE,
    # Upgraded from int8_float16 → float16. int8 quantization was losing
    # ~30-40% of content on Vietnamese meeting audio (silence hallucination
    # "Ừm. Ừm." plus dropped/repeated segments). float16 uses ~6GB VRAM
    # instead of ~3GB but T4 still has 15GB headroom with pyannote loaded.
    compute_type="float16" if DEVICE == "cuda" else "int8",
    download_root="/kaggle/working/models",
)
log.info("✓ faster-whisper large-v3 loaded")

# ── 2. Diarization: pyannote 3.1 ────────────────────────────────
from pyannote.audio import Pipeline as DiarizePipeline
from pyannote.audio import Inference as PyannoteInference
from pyannote.audio import Model as PyannoteModel

log.info("Loading pyannote speaker-diarization-3.1...")
DIARIZE = DiarizePipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1", token=HF_TOKEN,
)
if DEVICE == "cuda":
    DIARIZE.to(torch.device("cuda"))
log.info("✓ Pyannote diarization loaded")

# ── 3. Speaker embeddings: wespeaker 256-dim ────────────────────
log.info("Loading wespeaker embedding model...")
EMB_MODEL = PyannoteModel.from_pretrained(
    "pyannote/wespeaker-voxceleb-resnet34-LM", token=HF_TOKEN,
)
if DEVICE == "cuda":
    EMB_MODEL.to(torch.device("cuda"))
EMB_INFER = PyannoteInference(EMB_MODEL, window="whole", device=torch.device(DEVICE))
log.info("✓ Wespeaker embedder loaded")

print("\n" + "="*50)
print("✅ ALL MODELS LOADED — ready to serve")
print("="*50)


# ════════════════════════════════════════════════════════════════════
# CELL 3 — FastAPI server (endpoint tương thích OpenAI + words[])
# ════════════════════════════════════════════════════════════════════

import io
import json
import tempfile
import numpy as np
import soundfile as sf
import torchaudio
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="faster-whisper + pyannote (Kaggle)")


# ── Helper functions ─────────────────────────────────────────────

def _load_audio(path: str):
    """Load audio → mono 16kHz numpy + torch (pyannote needs both shapes)."""
    waveform, sr = torchaudio.load(path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != SAMPLE_RATE:
        waveform = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(waveform)
    return waveform, SAMPLE_RATE


def _compute_cluster_embeddings(waveform, sr, diarize_segs):
    """Per-speaker 256-dim voice embedding (averaged from up to 10s of turns)."""
    by_spk = {}
    for seg in diarize_segs:
        by_spk.setdefault(seg["speaker"], []).append(seg)

    out = {}
    for spk, segs in by_spk.items():
        segs_sorted = sorted(segs, key=lambda d: d["end"] - d["start"], reverse=True)
        chunks = []
        total = 0.0
        for s in segs_sorted:
            dur = s["end"] - s["start"]
            if dur < 0.2:
                continue
            take = min(dur, MAX_EMBED_SECONDS - total)
            if take <= 0:
                break
            chunks.append(waveform[:, int(s["start"]*sr):int((s["start"]+take)*sr)])
            total += take
            if total >= MAX_EMBED_SECONDS:
                break
        if not chunks:
            continue
        clip = torch.cat(chunks, dim=1)
        if clip.shape[1] < int(0.5 * sr):
            continue
        try:
            emb = EMB_INFER({"waveform": clip, "sample_rate": sr})
            emb_np = emb.numpy() if hasattr(emb, "numpy") else emb
            out[spk] = emb_np.flatten().tolist()
        except Exception as e:
            log.warning(f"Embedding failed for {spk}: {e}")
    return out


def _merge_diarize_turns(diarize_segs, min_dur=0.4, max_gap=0.5):
    """Clean overlaps + merge consecutive same-speaker turns."""
    if not diarize_segs:
        return []
    segs = sorted(diarize_segs, key=lambda d: (d["start"], d["end"]))

    resolved = []
    for i, d in enumerate(segs):
        s, e, spk = d["start"], d["end"], d["speaker"]
        for j in range(i + 1, len(segs)):
            nxt = segs[j]
            if nxt["start"] >= e:
                break
            if nxt["speaker"] != spk:
                e = min(e, nxt["start"])
        if e - s < min_dur:
            continue
        resolved.append({"speaker": spk, "start": s, "end": e})

    merged = []
    for r in resolved:
        if merged and merged[-1]["speaker"] == r["speaker"] and (r["start"] - merged[-1]["end"]) <= max_gap:
            merged[-1]["end"] = r["end"]
        else:
            merged.append(dict(r))
    return merged


def _word_speaker(start_s, end_s, turns):
    """Find which speaker turn contains this word's midpoint. Closest-fit fallback."""
    if start_s is None and end_s is None:
        return "SPEAKER_UNKNOWN"
    pt = ((start_s or 0) + (end_s or 0)) / 2 if (start_s is not None and end_s is not None) else (start_s if start_s is not None else end_s)
    for t in turns:
        if t["start"] <= pt <= t["end"]:
            return t["speaker"]
    if not turns:
        return "SPEAKER_UNKNOWN"
    return min(turns, key=lambda t: min(abs(t["start"] - pt), abs(t["end"] - pt)))["speaker"]


def _group_words_by_speaker(words_with_spk, full_text_fallback, total_dur):
    """Group consecutive same-speaker words into segments. Each segment keeps
    the per-word list so the FE can highlight word-by-word during playback."""
    if not words_with_spk:
        if not full_text_fallback:
            return [], []
        return (
            [{
                "speaker": "SPEAKER_UNKNOWN",
                "text": full_text_fallback,
                "start": 0.0,
                "end": total_dur,
                "words": [],
            }],
            [full_text_fallback],
        )

    segments_out, text_lines = [], []
    cur_spk = None
    cur_words: list[dict] = []
    cur_text_parts: list[str] = []
    cur_start = None
    cur_end = None

    def _flush():
        if not cur_spk or not cur_words:
            return
        text = " ".join(cur_text_parts).strip()
        if not text:
            return
        segments_out.append({
            "speaker": cur_spk,
            "text": text,
            "start": cur_start if cur_start is not None else 0.0,
            "end": cur_end if cur_end is not None else (cur_start or 0.0),
            "words": list(cur_words),
        })
        text_lines.append(f"{cur_spk}: {text}")

    for w in words_with_spk:
        spk = w["speaker"]
        if spk != cur_spk:
            _flush()
            cur_spk = spk
            cur_words = []
            cur_text_parts = []
            cur_start = w["start"]
            cur_end = w["end"]
        cur_words.append({"text": w["text"], "start": w["start"], "end": w["end"]})
        cur_text_parts.append(w["text"])
        cur_end = w["end"]
    _flush()

    return segments_out, text_lines


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "model": "faster-whisper-large-v3", "gpu": torch.cuda.is_available()}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "asr_model": "faster-whisper-large-v3 (int8_float16)",
        "diarize_model": "pyannote/speaker-diarization-3.1",
        "embedding_dim": 256,
        "supports_word_timestamps": True,
    }


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    prompt: Optional[str] = Form(default=None),
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
    model: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
):
    """OpenAI-compatible /v1/audio/transcriptions + diarization + word timestamps.

    Response augmented with `segments[].words[]` carrying `{text, start, end}`
    in absolute seconds. FE Notta view uses these for word-by-word highlight.
    """
    audio_bytes = await file.read()
    size_mb = len(audio_bytes) / 1024 / 1024
    log.info(f"Received {size_mb:.1f}MB audio")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        # 1. Load audio
        waveform, sr = _load_audio(tmp_path)
        audio_np = waveform.squeeze(0).numpy()
        duration = float(audio_np.shape[0]) / sr
        log.info(f"Audio: {duration:.1f}s, {sr}Hz")

        # 2. Resample sanity check
        if sr != SAMPLE_RATE:
            raise HTTPException(status_code=400, detail=f"Audio resample failed (sr={sr})")

        # 3. Diarization
        diarize_input = {"waveform": waveform, "sample_rate": sr}
        d_kwargs = {}
        if min_speakers is not None: d_kwargs["min_speakers"] = min_speakers
        if max_speakers is not None: d_kwargs["max_speakers"] = max_speakers

        log.info("Running pyannote diarization...")
        annotation = DIARIZE(diarize_input, **d_kwargs)
        if hasattr(annotation, "speaker_diarization"):
            annotation = annotation.speaker_diarization
        elif hasattr(annotation, "diarization"):
            annotation = annotation.diarization

        diarize_segs = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            diarize_segs.append({
                "speaker": speaker, "start": float(turn.start), "end": float(turn.end),
            })
        unique_spks = sorted({d["speaker"] for d in diarize_segs})
        log.info(f"Diarize: {len(diarize_segs)} turns, {len(unique_spks)} speakers")

        # 4. Speaker embeddings
        cluster_embeddings = _compute_cluster_embeddings(waveform, sr, diarize_segs)
        log.info(f"Embeddings: {len(cluster_embeddings)}/{len(unique_spks)} clusters")

        # 5. Merge turns
        merged_turns = _merge_diarize_turns(diarize_segs)
        log.info(f"Merged into {len(merged_turns)} turns")

        # 6. ASR with word timestamps (faster-whisper does DTW from attention,
        #    fits in T4 VRAM unlike HF pipeline which exploded the
        #    cross_attentions tensor for chunks >2 min).
        log.info("Running faster-whisper ASR with word timestamps...")
        segments_gen, info = ASR.transcribe(
            audio_np,
            language=language or "vi",
            word_timestamps=True,
            vad_filter=True,
            # Tighter VAD so genuine silence is skipped (no "Ừm. Ừm. Ừm."
            # hallucinations like we saw at 3:00-3:45 on AI_Innovation_16phut)
            # but short between-sentence pauses aren't accidentally dropped.
            # Config locked at "Iter 5" (fp16 + VAD 0.45 + no_speech 0.7).
            # Aggressive params from later iterations (VAD 0.35,
            # compression_ratio 2.0, log_prob -0.7) did not measurably
            # improve recall on AI_Innovation_16phut.flac while raising
            # the risk of dropping genuine quiet speech. Keep this as the
            # known-good baseline; revisit only with a wider eval corpus.
            vad_parameters={
                "threshold": 0.45,
                "min_silence_duration_ms": 500,
                "speech_pad_ms": 200,
            },
            initial_prompt=prompt or None,
            beam_size=5,
            condition_on_previous_text=False,
            no_speech_threshold=0.7,
        )

        # Materialize the generator (faster-whisper is lazy) + flatten words.
        all_words: list[dict] = []
        full_text_parts: list[str] = []
        for seg in segments_gen:
            seg_text = (seg.text or "").strip()
            if seg_text:
                full_text_parts.append(seg_text)
            if not seg.words:
                continue
            for w in seg.words:
                txt = (w.word or "").strip()
                if not txt:
                    continue
                all_words.append({
                    "text": txt,
                    "start": float(w.start),
                    "end": float(w.end),
                })
        full_text = " ".join(full_text_parts).strip()
        log.info(f"ASR: {len(all_words)} words, {len(full_text)} chars (lang={info.language})")

        # 7. Assign each word to a speaker via diarize turns + group into
        #    per-speaker segments (with per-word timestamps preserved).
        words_with_spk = [
            {**w, "speaker": _word_speaker(w["start"], w["end"], merged_turns)}
            for w in all_words
        ]
        segments_out, text_lines = _group_words_by_speaker(
            words_with_spk, full_text_fallback=full_text, total_dur=duration,
        )

        log.info(f"✓ Done: {len(segments_out)} segments, {len(unique_spks)} speakers")
        return JSONResponse({
            "text": "\n".join(text_lines),
            "language": info.language or language or "vi",
            "duration": duration,
            "segments": segments_out,
            "cluster_embeddings": cluster_embeddings,
        })

    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try: os.unlink(tmp_path)
        except: pass


# ── Streaming endpoint (SSE) — Notta-style progressive output ──────────
# Yields each ASR segment as it lands instead of buffering until the full
# audio is decoded. Diarization runs first (it's batch — pyannote needs
# the whole file to cluster speakers), then ASR streams over it; each
# segment carries its speaker label + per-word timestamps. The Mee
# backend proxies this stream straight to the FE EventSource.

from fastapi.responses import StreamingResponse

def _sse(event_obj: dict) -> str:
    """Serialize one SSE 'message' frame. EventSource API splits on the
    `data:` prefix and double newline, so we keep both."""
    return f"data: {json.dumps(event_obj, ensure_ascii=False)}\n\n"


@app.post("/v1/audio/transcriptions/stream")
async def transcribe_stream(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default=None),
    prompt: Optional[str] = Form(default=None),
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
):
    """Same as /v1/audio/transcriptions but emits Server-Sent Events.

    Event sequence:
      {type:"meta",     duration, language}         — first frame
      {type:"diarize",  turns:[...], embeddings}    — after pyannote runs
      {type:"segment",  speaker, text, start, end,  — one per ASR segment
                        words:[{text,start,end}]}     (yielded live)
      {type:"done",     segments_count}             — terminator
      {type:"error",    detail}                     — on failure
    """
    audio_bytes = await file.read()
    size_mb = len(audio_bytes) / 1024 / 1024
    log.info(f"[STREAM] Received {size_mb:.1f}MB audio")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    async def event_stream():
        try:
            # Padding comment first — Cloudflare's free tunnel + uvicorn
            # buffer up to ~4KB before flushing. A 2KB SSE comment forces
            # an immediate flush so the FE's reader.read() returns frame-
            # by-frame instead of in one big delayed burst (which would
            # collapse the Notta word-reveal animation into "everything
            # appears at once"). EventSource clients ignore `:` lines.
            yield ":" + (" " * 2048) + "\n\n"

            # 1. Load + diarize (batch — must finish before streaming ASR so
            #    each emitted segment already has its speaker).
            waveform, sr = _load_audio(tmp_path)
            audio_np = waveform.squeeze(0).numpy()
            duration = float(audio_np.shape[0]) / sr
            yield _sse({"type": "meta", "duration": duration, "language": language or "vi"})

            d_kwargs = {}
            if min_speakers is not None: d_kwargs["min_speakers"] = min_speakers
            if max_speakers is not None: d_kwargs["max_speakers"] = max_speakers
            log.info("[STREAM] diarize…")
            annotation = DIARIZE({"waveform": waveform, "sample_rate": sr}, **d_kwargs)
            if hasattr(annotation, "speaker_diarization"):
                annotation = annotation.speaker_diarization
            elif hasattr(annotation, "diarization"):
                annotation = annotation.diarization
            diarize_segs = []
            for turn, _, speaker in annotation.itertracks(yield_label=True):
                diarize_segs.append({
                    "speaker": speaker, "start": float(turn.start), "end": float(turn.end),
                })
            cluster_embeddings = _compute_cluster_embeddings(waveform, sr, diarize_segs)
            merged_turns = _merge_diarize_turns(diarize_segs)
            yield _sse({
                "type": "diarize",
                "turns": merged_turns,
                "embeddings": cluster_embeddings,
            })

            # 2. ASR streaming — faster-whisper returns a generator; iterate
            #    and emit each segment as it's produced. ~3-5s to first
            #    segment vs ~60s for full audio in batch mode.
            log.info("[STREAM] ASR streaming…")
            segments_gen, info = ASR.transcribe(
                audio_np,
                language=language or "vi",
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={
                    "threshold": 0.45,
                    "min_silence_duration_ms": 500,
                    "speech_pad_ms": 200,
                },
                initial_prompt=prompt or None,
                beam_size=5,
                # Disable context spillover — see batch endpoint comment.
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )
            import asyncio
            seg_count = 0
            for seg in segments_gen:
                seg_text = (seg.text or "").strip()
                if not seg_text:
                    continue
                words_payload = []
                if seg.words:
                    for w in seg.words:
                        txt = (w.word or "").strip()
                        if not txt:
                            continue
                        words_payload.append({
                            "text": txt,
                            "start": float(w.start),
                            "end": float(w.end),
                        })
                # Resolve speaker via diarize turns + word midpoint.
                mid_s = (float(seg.start) + float(seg.end)) / 2
                spk = _word_speaker(mid_s, mid_s, merged_turns) if merged_turns else "SPEAKER_UNKNOWN"
                yield _sse({
                    "type": "segment",
                    "speaker": spk,
                    "text": seg_text,
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "words": words_payload,
                })
                # Yield control to the event loop so uvicorn's writer can
                # actually flush the chunk before the next iteration eats
                # CPU on faster-whisper's decode. Without this, the entire
                # iteration runs synchronously and all SSE frames pile up
                # in one network packet.
                await asyncio.sleep(0)
                seg_count += 1

            yield _sse({"type": "done", "segments_count": seg_count})
            log.info(f"[STREAM] ✓ Done: {seg_count} segments")

        except Exception as e:
            log.exception("[STREAM] failed")
            yield _sse({"type": "error", "detail": str(e)})
        finally:
            try: os.unlink(tmp_path)
            except: pass

    # Explicit SSE headers — Cloudflare's free tunnel buffers responses by
    # default, which collapses the stream into one giant response (or 520s
    # when the buffer fills before EOF). `X-Accel-Buffering: no` is honoured
    # by most reverse proxies including cloudflared; `Cache-Control: no-cache`
    # plus `Connection: keep-alive` close the remaining buffering loopholes.
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ════════════════════════════════════════════════════════════════════
# CELL 4 — Cloudflare tunnel (free, anonymous, URL tự sinh)
# ════════════════════════════════════════════════════════════════════

# import subprocess, time, re, threading
#
# !curl -sL -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
# !chmod +x cloudflared
#
# # Start uvicorn in background
# import uvicorn
# uvicorn_proc = threading.Thread(
#     target=lambda: uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info"),
#     daemon=True,
# )
# uvicorn_proc.start()
# time.sleep(3)
#
# # Tunnel
# tunnel_proc = subprocess.Popen(
#     ["./cloudflared", "tunnel", "--url", "http://localhost:8000"],
#     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
# )
#
# PUBLIC_URL = None
# for _ in range(30):
#     line = tunnel_proc.stdout.readline()
#     if not line:
#         time.sleep(0.5)
#         continue
#     print(line, end="")
#     m = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
#     if m:
#         PUBLIC_URL = m.group(1)
#         break
#
# print(f"\n\n🌐 PUBLIC URL: {PUBLIC_URL}")
# print(f"Set in your project .env:")
# print(f"  FASTER_WHISPER_BASE_URL={PUBLIC_URL}")


# ════════════════════════════════════════════════════════════════════
# CELL 5 — Keepalive (chống Kaggle idle-kick)
# ════════════════════════════════════════════════════════════════════

# import time
# print("Keepalive running. Leave this tab open to keep the kernel alive.")
# while True:
#     time.sleep(60)
#     print(".", end="", flush=True)
