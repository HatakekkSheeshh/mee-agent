"""Mee unified STT + diarization server — self-hosted on nhihb-gpu-2080 (RTX 2080 Ti, 11GB).

ONE OpenAI-compatible endpoint that serves BOTH STT backends with on-demand
(single-slot) model loading, plus pyannote diarization that stays resident:

    pyannote 3.1            → RESIDENT in VRAM (~1.5-2GB)   [diarization + 256-d embeddings]
    faster-whisper large-v3 → loaded on demand (~6GB fp16)  [CTranslate2, word timestamps]
    PhoWhisper-large        → loaded on demand (~5-6GB)      [HF transformers, VN fine-tune]

Why on-demand: 11GB VRAM can't hold both large STT models + pyannote at once
(~13-14GB). So at most ONE STT backend lives in VRAM; switching backend unloads
the other (`del` + `torch.cuda.empty_cache()`). First request after a switch
pays a ~15-30s reload; same-backend requests are warm.

Backend is chosen per-request from the `model` form field:
    "model" contains "pho"  → PhoWhisper       (e.g. "phowhisper", "vinai/PhoWhisper-large")
    otherwise               → faster-whisper    (e.g. "faster-whisper", "large-v3")

This matches Mee's model_registry.py profiles (PHOWHISPER_* and FASTER_WHISPER_*
can both point at this one server) AND the benchmark clients
(benchmarks/clients/{phowhisper,faster_whisper}.py).

────────────────────────────────────────────────────────────────────────────
RUN (after NVIDIA driver + deps are installed — see README.md):

    export HF_TOKEN=hf_xxxx              # accepted ToS for the 3 pyannote pages
    export SERVER_TOKEN=$(openssl rand -hex 24)   # optional bearer auth
    python mee_stt_server.py             # listens on :9100

ENV VARS:
    HF_TOKEN          (required) HuggingFace token w/ pyannote ToS accepted
    PORT              (default 9100)
    SERVER_TOKEN      (optional) if set, requests must send Authorization: Bearer <token>
    STT_BACKENDS      (default "faster_whisper,phowhisper") comma list of enabled backends
    PRELOAD_STT       (optional) backend name to eager-load at startup (benchmark fairness)
    FASTER_WHISPER_MODEL  (default "large-v3")
    FASTER_WHISPER_COMPUTE (default "float16" on cuda — int8 loses VN content, see note)
    PHOWHISPER_MODEL  (default "vinai/PhoWhisper-large")
    DIARIZE_MODEL     (default "pyannote/speaker-diarization-3.1")
    EMBEDDING_MODEL   (default "pyannote/wespeaker-voxceleb-resnet34-LM")

ENDPOINTS:
    GET  /health                       → status + which backend is currently resident
    POST /v1/audio/transcriptions      → OpenAI-compatible; returns
                                         {text, language, duration, segments[], cluster_embeddings}
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import tempfile
import threading
from typing import Optional

import torch
import torchaudio
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mee_stt")

# ───── Config ─────────────────────────────────────────────────────────
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
SAMPLE_RATE = 16000
MAX_EMBED_SECONDS = 10.0

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "9100"))
SERVER_TOKEN = os.getenv("SERVER_TOKEN", "").strip()
STT_BACKENDS = [b.strip() for b in os.getenv("STT_BACKENDS", "faster_whisper,phowhisper").split(",") if b.strip()]
PRELOAD_STT = os.getenv("PRELOAD_STT", "").strip()

FASTER_WHISPER_MODEL = os.getenv("FASTER_WHISPER_MODEL", "large-v3")
# int8 quantization dropped ~30-40% of VN meeting content (silence hallucination
# + dropped segments) in Sprint 04 testing — float16 is the known-good baseline.
# 2080 Ti has 11GB; fp16 faster-whisper (~6GB) + pyannote (~2GB) fits.
FASTER_WHISPER_COMPUTE = os.getenv("FASTER_WHISPER_COMPUTE", "float16" if DEVICE == "cuda" else "int8")
PHOWHISPER_MODEL = os.getenv("PHOWHISPER_MODEL", "vinai/PhoWhisper-large")
DIARIZE_MODEL = os.getenv("DIARIZE_MODEL", "pyannote/speaker-diarization-3.1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "pyannote/wespeaker-voxceleb-resnet34-LM")

# Serialize everything: a single 11GB GPU can't run two transcriptions or a
# model swap concurrently. One global lock keeps model load/unload + inference
# atomic. Endpoints are sync `def` so FastAPI runs them in its threadpool and
# the lock actually blocks (rather than yielding the event loop mid-swap).
_GPU_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════
# Resident models — pyannote diarization + speaker embedding (loaded once)
# ═══════════════════════════════════════════════════════════════════════

DIARIZE = None      # pyannote Pipeline
EMB_INFER = None    # pyannote Inference (wespeaker, 256-d)


def _load_pyannote() -> None:
    """Load diarization + embedding models into VRAM once at startup."""
    global DIARIZE, EMB_INFER
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN env var required for pyannote. Token must have accepted ToS at:\n"
            "  https://huggingface.co/pyannote/speaker-diarization-3.1\n"
            "  https://huggingface.co/pyannote/segmentation-3.0\n"
            "  https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM"
        )
    import inspect

    from pyannote.audio import Inference as PyannoteInference
    from pyannote.audio import Model as PyannoteModel
    from pyannote.audio import Pipeline as DiarizePipeline

    # pyannote renamed the auth kwarg across versions: 3.3.x uses `use_auth_token`,
    # some builds use `token`. Pick whichever the installed signature accepts.
    def _auth_kw(func) -> str:
        params = inspect.signature(func).parameters
        return "token" if "token" in params else "use_auth_token"

    log.info(f"Loading pyannote diarization: {DIARIZE_MODEL}")
    DIARIZE = DiarizePipeline.from_pretrained(
        DIARIZE_MODEL, **{_auth_kw(DiarizePipeline.from_pretrained): HF_TOKEN}
    )
    if DEVICE == "cuda":
        DIARIZE.to(torch.device("cuda"))

    log.info(f"Loading speaker embedding: {EMBEDDING_MODEL}")
    emb_model = PyannoteModel.from_pretrained(
        EMBEDDING_MODEL, **{_auth_kw(PyannoteModel.from_pretrained): HF_TOKEN}
    )
    if DEVICE == "cuda":
        emb_model.to(torch.device("cuda"))
    EMB_INFER = PyannoteInference(emb_model, window="whole", device=torch.device(DEVICE))
    log.info("✓ pyannote diarization + embedding resident")


# ═══════════════════════════════════════════════════════════════════════
# On-demand STT backends — at most ONE resident at a time (single-slot LRU)
# ═══════════════════════════════════════════════════════════════════════

class _STTSlot:
    """Holds the single currently-loaded STT backend. Swapping evicts the old
    model from VRAM before loading the new one."""

    def __init__(self) -> None:
        self.name: Optional[str] = None   # "faster_whisper" | "phowhisper"
        self.model = None                 # backend-specific object

    def _evict(self) -> None:
        if self.model is None:
            return
        log.info(f"Evicting STT backend '{self.name}' from VRAM")
        self.model = None
        self.name = None
        gc.collect()
        if DEVICE == "cuda":
            torch.cuda.empty_cache()

    def get(self, backend: str):
        """Return the loaded model for `backend`, swapping if needed."""
        if backend not in STT_BACKENDS:
            raise HTTPException(
                status_code=400,
                detail=f"STT backend '{backend}' not enabled. Enabled: {STT_BACKENDS}",
            )
        if self.name == backend and self.model is not None:
            return self.model
        self._evict()
        log.info(f"Loading STT backend '{backend}' (compute on {DEVICE})…")
        if backend == "faster_whisper":
            self.model = _load_faster_whisper()
        elif backend == "phowhisper":
            self.model = _load_phowhisper()
        else:
            raise HTTPException(status_code=400, detail=f"Unknown STT backend '{backend}'")
        self.name = backend
        log.info(f"✓ STT backend '{backend}' resident")
        return self.model


_STT = _STTSlot()


def _load_faster_whisper():
    """faster-whisper (CTranslate2). Returns a WhisperModel."""
    from faster_whisper import WhisperModel

    return WhisperModel(
        FASTER_WHISPER_MODEL,
        device=DEVICE,
        compute_type=FASTER_WHISPER_COMPUTE,
    )


def _load_phowhisper():
    """PhoWhisper via HF transformers ASR pipeline."""
    from transformers import pipeline as hf_pipeline

    dtype = torch.float16 if DEVICE == "cuda" else torch.float32
    return hf_pipeline(
        "automatic-speech-recognition",
        model=PHOWHISPER_MODEL,
        torch_dtype=dtype,
        device=DEVICE,
        chunk_length_s=30,        # PhoWhisper trained on 30s chunks
        return_timestamps="word", # word-level for diarization alignment
    )


def _pick_backend(model_field: Optional[str]) -> str:
    """Map the OpenAI `model` form field → backend name."""
    m = (model_field or "").lower()
    if "pho" in m:
        return "phowhisper"
    if "faster" in m or "whisper" in m or "large-v3" in m:
        return "faster_whisper"
    # Default: preloaded backend, else first enabled.
    if PRELOAD_STT in STT_BACKENDS:
        return PRELOAD_STT
    return STT_BACKENDS[0] if STT_BACKENDS else "faster_whisper"


# ═══════════════════════════════════════════════════════════════════════
# Audio + diarization helpers (shared by both backends)
# ═══════════════════════════════════════════════════════════════════════

def _load_audio(path: str) -> tuple[torch.Tensor, int]:
    """Any audio → mono 16kHz tensor (torchaudio handles wav/flac/mp3/m4a via ffmpeg)."""
    waveform, sr = torchaudio.load(path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != SAMPLE_RATE:
        waveform = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(waveform)
    return waveform, SAMPLE_RATE


def _run_diarization(waveform: torch.Tensor, sr: int, min_spk, max_spk) -> list[dict]:
    diarize_input = {"waveform": waveform, "sample_rate": sr}
    d_kwargs = {}
    if min_spk is not None:
        d_kwargs["min_speakers"] = min_spk
    if max_spk is not None:
        d_kwargs["max_speakers"] = max_spk
    annotation = DIARIZE(diarize_input, **d_kwargs)
    # pyannote 3.3+ wraps the Annotation
    if hasattr(annotation, "speaker_diarization"):
        annotation = annotation.speaker_diarization
    elif hasattr(annotation, "diarization"):
        annotation = annotation.diarization
    segs = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segs.append({"speaker": speaker, "start": float(turn.start), "end": float(turn.end)})
    return segs


def _compute_cluster_embeddings(waveform, sr, diarize_segs) -> dict[str, list[float]]:
    """Per-speaker 256-d voice embedding (avg of up to MAX_EMBED_SECONDS of turns)."""
    by_spk: dict[str, list[dict]] = {}
    for seg in diarize_segs:
        by_spk.setdefault(seg["speaker"], []).append(seg)

    out: dict[str, list[float]] = {}
    for spk, segs in by_spk.items():
        segs_sorted = sorted(segs, key=lambda d: d["end"] - d["start"], reverse=True)
        chunks, total = [], 0.0
        for s in segs_sorted:
            dur = s["end"] - s["start"]
            if dur < 0.2:
                continue
            take = min(dur, MAX_EMBED_SECONDS - total)
            if take <= 0:
                break
            chunks.append(waveform[:, int(s["start"] * sr):int((s["start"] + take) * sr)])
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


def _merge_diarize_turns(diarize_segs, min_dur=0.4, max_gap=0.5) -> list[dict]:
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


def _word_speaker(start_s, end_s, turns) -> str:
    """Speaker turn containing this word's midpoint; nearest-turn fallback."""
    if start_s is None and end_s is None:
        return "SPEAKER_UNKNOWN"
    if start_s is not None and end_s is not None:
        pt = (start_s + end_s) / 2
    else:
        pt = start_s if start_s is not None else end_s
    for t in turns:
        if t["start"] <= pt <= t["end"]:
            return t["speaker"]
    if not turns:
        return "SPEAKER_UNKNOWN"
    return min(turns, key=lambda t: min(abs(t["start"] - pt), abs(t["end"] - pt)))["speaker"]


def _group_words_by_speaker(words_with_spk, full_text_fallback, total_dur):
    """Group consecutive same-speaker words into segments (keep per-word ts)."""
    if not words_with_spk:
        if not full_text_fallback:
            return [], []
        return (
            [{"speaker": "SPEAKER_UNKNOWN", "text": full_text_fallback,
              "start": 0.0, "end": total_dur, "words": []}],
            [full_text_fallback],
        )

    segments_out, text_lines = [], []
    cur_spk = None
    cur_words: list[dict] = []
    cur_text_parts: list[str] = []
    cur_start = cur_end = None

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


# ═══════════════════════════════════════════════════════════════════════
# Per-backend ASR → normalized words[]  ({text, start, end})
# ═══════════════════════════════════════════════════════════════════════

def _asr_faster_whisper(model, audio_np, language, prompt) -> tuple[list[dict], str]:
    """faster-whisper transcribe → flat words[] + full text."""
    segments_gen, info = model.transcribe(
        audio_np,
        language=language or "vi",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"threshold": 0.45, "min_silence_duration_ms": 500, "speech_pad_ms": 200},
        initial_prompt=prompt or None,
        beam_size=5,
        condition_on_previous_text=False,
        no_speech_threshold=0.7,
    )
    words, text_parts = [], []
    for seg in segments_gen:
        seg_text = (seg.text or "").strip()
        if seg_text:
            text_parts.append(seg_text)
        for w in (seg.words or []):
            txt = (w.word or "").strip()
            if not txt:
                continue
            words.append({"text": txt, "start": float(w.start), "end": float(w.end)})
    return words, " ".join(text_parts).strip(), (info.language or language or "vi")


def _asr_phowhisper(model, audio_np, language, prompt) -> tuple[list[dict], str]:
    """PhoWhisper (HF pipeline) → flat words[] + full text. Carries forward the
    previous word's end when a chunk timestamp is None (boundary words)."""
    generate_kwargs = {"language": language or "vi", "task": "transcribe"}
    if prompt:
        try:
            prompt_ids = model.tokenizer.get_prompt_ids(prompt, return_tensors="pt")
            if DEVICE == "cuda":
                prompt_ids = prompt_ids.to("cuda")
            generate_kwargs["prompt_ids"] = prompt_ids
        except Exception as e:
            log.warning(f"prompt encoding failed, skipping: {e}")

    result = model(audio_np, generate_kwargs=generate_kwargs, return_timestamps="word")
    full_text = (result.get("text") or "").strip()
    words = []
    last_end = 0.0
    for c in (result.get("chunks") or []):
        txt = (c.get("text") or "").strip()
        if not txt:
            continue
        ts = c.get("timestamp") or (None, None)
        s, e = ts if ts else (None, None)
        s = s if s is not None else last_end
        e = e if e is not None else s
        words.append({"text": txt, "start": float(s), "end": float(e)})
        last_end = e
    return words, full_text, (language or "vi")


# ═══════════════════════════════════════════════════════════════════════
# FastAPI app
# ═══════════════════════════════════════════════════════════════════════

app = FastAPI(title="Mee STT + diarization (nhihb-gpu-2080)")


@app.on_event("startup")
def _startup() -> None:
    log.info(f"Device: {DEVICE}")
    if DEVICE == "cuda":
        log.info(f"GPU: {torch.cuda.get_device_name(0)} "
                 f"({torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB)")
    else:
        log.warning("CUDA not available — running on CPU (very slow; for syntax/dev only)")
    log.info(f"Enabled STT backends: {STT_BACKENDS}")
    _load_pyannote()
    if PRELOAD_STT:
        with _GPU_LOCK:
            _STT.get(PRELOAD_STT)


def _check_auth(authorization: Optional[str]) -> None:
    if SERVER_TOKEN and authorization != f"Bearer {SERVER_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "gpu": torch.cuda.get_device_name(0) if DEVICE == "cuda" else None,
        "enabled_backends": STT_BACKENDS,
        "resident_stt": _STT.name,
        "diarize_model": DIARIZE_MODEL,
        "embedding_dim": 256,
        "auth_required": bool(SERVER_TOKEN),
    }


@app.post("/v1/audio/transcriptions")
def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default="vi"),
    prompt: Optional[str] = Form(default=None),
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
    model: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
    authorization: Optional[str] = Header(default=None),
):
    """OpenAI-compatible STT + pyannote diarization + per-cluster embeddings.

    Response: {text, language, duration, segments[{speaker,text,start,end,words[]}],
               cluster_embeddings{spk: [256 floats]}}
    """
    _check_auth(authorization)
    backend = _pick_backend(model)

    audio_bytes = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with _GPU_LOCK:
            waveform, sr = _load_audio(tmp_path)
            audio_np = waveform.squeeze(0).numpy()
            duration = float(audio_np.shape[0]) / sr
            log.info(f"[{backend}] audio={duration:.1f}s {sr}Hz, size={len(audio_bytes)/1e6:.1f}MB")

            # 1. Diarization (pyannote — resident)
            diarize_segs = _run_diarization(waveform, sr, min_speakers, max_speakers)
            unique = sorted({d["speaker"] for d in diarize_segs})
            log.info(f"[{backend}] diarize: {len(diarize_segs)} turns, {len(unique)} speakers")
            cluster_embeddings = _compute_cluster_embeddings(waveform, sr, diarize_segs)
            merged_turns = _merge_diarize_turns(diarize_segs)

            # 2. ASR (on-demand backend)
            stt = _STT.get(backend)
            if backend == "faster_whisper":
                words, full_text, lang = _asr_faster_whisper(stt, audio_np, language, prompt)
            else:
                words, full_text, lang = _asr_phowhisper(stt, audio_np, language, prompt)
            log.info(f"[{backend}] ASR: {len(words)} words, {len(full_text)} chars (lang={lang})")

            # 3. Assign each word to a speaker turn + group into segments
            words_with_spk = [
                {**w, "speaker": _word_speaker(w["start"], w["end"], merged_turns)}
                for w in words
            ]
            segments_out, text_lines = _group_words_by_speaker(
                words_with_spk, full_text_fallback=full_text, total_dur=duration,
            )

        return JSONResponse({
            "text": "\n".join(text_lines),
            "language": lang,
            "duration": duration,
            "segments": segments_out,
            "cluster_embeddings": cluster_embeddings,
        })
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _sse(obj: dict) -> str:
    """One SSE 'message' frame (EventSource splits on `data:` + blank line)."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/v1/audio/transcriptions/stream")
async def transcribe_stream(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default="vi"),
    prompt: Optional[str] = Form(default=None),
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
    model: Optional[str] = Form(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """SSE-streamed transcription for the Notta-style progressive UI.

    Diarization runs first (batch — pyannote needs the whole file to cluster),
    then ASR segments stream out live. Only faster-whisper supports this (its
    transcribe() is a generator + has word timestamps); the `model` field is
    ignored here and faster-whisper is always used.

    Event sequence: meta → diarize → segment* → done  (or error).
    """
    _check_auth(authorization)
    audio_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    async def event_stream():
        acquired = False
        try:
            # Primer flush so the FE EventSource gets headers + first bytes
            # immediately (reverse proxies buffer until ~4KB otherwise).
            yield ":" + (" " * 2048) + "\n\n"

            # Acquire the GPU lock WITHOUT blocking the event loop (non-blocking
            # try + async sleep) so /health etc. stay responsive while waiting.
            while not _GPU_LOCK.acquire(blocking=False):
                await asyncio.sleep(0.1)
            acquired = True

            waveform, sr = _load_audio(tmp_path)
            audio_np = waveform.squeeze(0).numpy()
            duration = float(audio_np.shape[0]) / sr
            log.info(f"[stream] audio={duration:.1f}s")
            yield _sse({"type": "meta", "duration": duration, "language": language or "vi"})

            diarize_segs = _run_diarization(waveform, sr, min_speakers, max_speakers)
            cluster_embeddings = _compute_cluster_embeddings(waveform, sr, diarize_segs)
            merged_turns = _merge_diarize_turns(diarize_segs)
            log.info(f"[stream] diarize: {len(diarize_segs)} turns")
            yield _sse({"type": "diarize", "turns": merged_turns, "embeddings": cluster_embeddings})

            stt = _STT.get("faster_whisper")
            segments_gen, info = stt.transcribe(
                audio_np,
                language=language or "vi",
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"threshold": 0.45, "min_silence_duration_ms": 500, "speech_pad_ms": 200},
                initial_prompt=prompt or None,
                beam_size=5,
                condition_on_previous_text=False,
                no_speech_threshold=0.7,
            )
            seg_count = 0
            for seg in segments_gen:
                seg_text = (seg.text or "").strip()
                if not seg_text:
                    continue
                words_payload = [
                    {"text": (w.word or "").strip(), "start": float(w.start), "end": float(w.end)}
                    for w in (seg.words or []) if (w.word or "").strip()
                ]
                mid = (float(seg.start) + float(seg.end)) / 2
                spk = _word_speaker(mid, mid, merged_turns) if merged_turns else "SPEAKER_UNKNOWN"
                yield _sse({
                    "type": "segment", "speaker": spk, "text": seg_text,
                    "start": float(seg.start), "end": float(seg.end), "words": words_payload,
                })
                await asyncio.sleep(0)  # let uvicorn flush the frame
                seg_count += 1

            yield _sse({"type": "done", "segments_count": seg_count})
            log.info(f"[stream] ✓ {seg_count} segments")
        except Exception as e:
            log.exception("[stream] failed")
            yield _sse({"type": "error", "detail": str(e)})
        finally:
            if acquired:
                _GPU_LOCK.release()
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/audio/embed")
def embed(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(default=None),
):
    """256-d wespeaker voice embedding for a whole clip — for voiceprint
    enrollment + cross-meeting speaker matching.

    Reuses the resident EMB_INFER (no second model load), runs the audio
    through _load_audio (mono 16kHz, handles wav/flac/mp3/m4a/webm via
    torchaudio+ffmpeg). Returns {"embedding": [256 floats], "dim": 256}.
    """
    _check_auth(authorization)
    audio_bytes = file.file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        with _GPU_LOCK:
            waveform, sr = _load_audio(tmp_path)  # [1, N] mono 16kHz
            emb = EMB_INFER({"waveform": waveform, "sample_rate": sr})
            emb_np = emb.numpy() if hasattr(emb, "numpy") else emb
            vec = [float(x) for x in emb_np.flatten()]
        if len(vec) != 256:
            raise HTTPException(status_code=500, detail=f"embedding dim {len(vec)} != 256")
        return JSONResponse({"embedding": vec, "dim": len(vec)})
    except HTTPException:
        raise
    except Exception as e:
        log.exception("embed failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
