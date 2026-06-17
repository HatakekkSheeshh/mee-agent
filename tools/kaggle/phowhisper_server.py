"""PhoWhisper-large + pyannote 3.1 — Kaggle GPU notebook.

Copy each "CELL N" block into a separate Kaggle notebook cell and run in order.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP (one-time):
  1. Kaggle → New Notebook → Settings:
       • Accelerator = GPU T4 x2
       • Internet = ON
       • Persistence = Files only
  2. Add-ons → Secrets → thêm:
       HF_TOKEN      = hf_xxxxxxx  (HuggingFace token, đã accept ToS cho
                        pyannote/speaker-diarization-3.1 và
                        pyannote/wespeaker-voxceleb-resnet34-LM)
  3. Paste cells 1→5, chạy lần lượt
  4. Cell 4 sẽ in ra public URL → copy vào benchmarks/.env:
       PHOWHISPER_BASE_URL=https://xxxx.trycloudflare.com
  5. Chạy benchmark:   python benchmarks/run.py
  6. Giữ tab Kaggle mở — cell 5 (keepalive) giữ kernel không bị kill

LƯU Ý:
  • Kaggle kernel tối đa 12h, idle kick sau ~9-30 phút (keepalive chống idle)
  • GPU T4 x2 = 30GB VRAM tổng, PhoWhisper-large + pyannote dùng ~12GB
  • Lần đầu chạy cell 2 mất ~5-8 phút (download models), các lần sau cache
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ════════════════════════════════════════════════════════════════════
# CELL 1 — Install dependencies (~3 min lần đầu, cache sau đó)
# ════════════════════════════════════════════════════════════════════

# !pip install -q \
#     "pyannote.audio>=3.1" \
#     "transformers>=4.39" \
#     "accelerate" \
#     "torchaudio" \
#     "soundfile" \
#     "fastapi" \
#     "uvicorn[standard]" \
#     "python-multipart"


# ════════════════════════════════════════════════════════════════════
# CELL 2 — Load models lên GPU (~5-8 min lần đầu, ~30s sau đó)
# ════════════════════════════════════════════════════════════════════

import os
import logging
import torch

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("phowhisper")

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
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32
SAMPLE_RATE = 16000
MAX_EMBED_SECONDS = 10.0

print(f"CUDA: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

# ── 1. ASR: PhoWhisper-large ────────────────────────────────────
from transformers import pipeline as hf_pipeline

log.info("Loading PhoWhisper-large...")
ASR = hf_pipeline(
    "automatic-speech-recognition",
    model="vinai/PhoWhisper-large",
    torch_dtype=TORCH_DTYPE,
    device=DEVICE,
    chunk_length_s=30,
    return_timestamps=True,
)
log.info("✓ PhoWhisper-large loaded")

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
# CELL 3 — FastAPI server (endpoint tương thích OpenAI)
# ════════════════════════════════════════════════════════════════════

import io
import tempfile
import numpy as np
import soundfile as sf
import torchaudio
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="PhoWhisper + pyannote (Kaggle)")


# ── Helper functions ─────────────────────────────────────────────

def _load_audio(path: str):
    """Load audio → mono 16kHz tensor."""
    waveform, sr = torchaudio.load(path)
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != SAMPLE_RATE:
        waveform = torchaudio.transforms.Resample(sr, SAMPLE_RATE)(waveform)
    return waveform, SAMPLE_RATE


def _compute_cluster_embeddings(waveform, sr, diarize_segs):
    """Per-speaker 256-dim voice embedding."""
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
    """Clean + merge pyannote turns."""
    if not diarize_segs:
        return []
    segs = sorted(diarize_segs, key=lambda d: (d["start"], d["end"]))

    # Resolve overlaps
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

    # Merge consecutive same-speaker
    merged = []
    for r in resolved:
        if merged and merged[-1]["speaker"] == r["speaker"] and (r["start"] - merged[-1]["end"]) <= max_gap:
            merged[-1]["end"] = r["end"]
        else:
            merged.append(dict(r))
    return merged


def _word_speaker(word_ts, turns):
    """Find which speaker turn contains this word's midpoint."""
    s, e = word_ts if word_ts else (None, None)
    if s is None and e is None:
        return "SPEAKER_UNKNOWN"
    pt = (s + e) / 2 if (s is not None and e is not None) else (s if s is not None else e)
    for t in turns:
        if t["start"] <= pt <= t["end"]:
            return t["speaker"]
    if not turns:
        return "SPEAKER_UNKNOWN"
    return min(turns, key=lambda t: min(abs(t["start"] - pt), abs(t["end"] - pt)))["speaker"]


def _align_words_to_turns(word_chunks, turns, full_text_fallback, total_dur):
    """Group consecutive same-speaker words into segments."""
    if not word_chunks or all(not c.get("timestamp") for c in word_chunks):
        if not full_text_fallback:
            return [], []
        spk = turns[0]["speaker"] if turns else "SPEAKER_UNKNOWN"
        return (
            [{"speaker": spk, "text": full_text_fallback, "start": 0.0, "end": total_dur}],
            [f"{spk}: {full_text_fallback}"],
        )
    if not turns:
        return (
            [{"speaker": "SPEAKER_UNKNOWN", "text": full_text_fallback,
              "start": 0.0, "end": total_dur}],
            [full_text_fallback],
        )

    segments_out, text_lines = [], []
    cur_spk, cur_words, cur_start, cur_end = None, [], None, None

    def _flush():
        if cur_spk and cur_words:
            text = " ".join(cur_words).strip()
            if text:
                segments_out.append({
                    "speaker": cur_spk, "text": text,
                    "start": cur_start or 0.0, "end": cur_end or 0.0,
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


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "model": "PhoWhisper-large", "gpu": torch.cuda.is_available()}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": DEVICE,
        "asr_model": "vinai/PhoWhisper-large",
        "diarize_model": "pyannote/speaker-diarization-3.1",
        "embedding_dim": 256,
    }


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(default="vi"),
    prompt: Optional[str] = Form(default=None),
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
    model: Optional[str] = Form(default=None),
    response_format: Optional[str] = Form(default="json"),
):
    """OpenAI-compatible /v1/audio/transcriptions + speaker diarization."""
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
        duration = len(audio_np) / sr
        log.info(f"Audio: {duration:.1f}s, {sr}Hz")

        # 2. ASR kwargs
        generate_kwargs = {"language": language or "vi", "task": "transcribe"}
        if prompt:
            try:
                prompt_ids = ASR.tokenizer.get_prompt_ids(prompt, return_tensors="pt")
                if DEVICE == "cuda":
                    prompt_ids = prompt_ids.to("cuda")
                generate_kwargs["prompt_ids"] = prompt_ids
            except Exception as e:
                log.warning(f"Prompt encoding failed: {e}")

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

        # 6. ASR (segment-level timestamps — word-level OOMs cross_attentions
        # tensor on T4 15GB for clips >2 min. Segment-level is ~10× cheaper.)
        log.info("Running PhoWhisper ASR...")
        asr_result = ASR(audio_np, generate_kwargs=generate_kwargs, return_timestamps=True)
        full_text = (asr_result.get("text") or "").strip()
        word_chunks = asr_result.get("chunks", []) or []
        log.info(f"ASR: {len(word_chunks)} segments, {len(full_text)} chars")

        # 7. Align words to speaker turns
        segments_out, text_lines = _align_words_to_turns(
            word_chunks, merged_turns, full_text, total_dur=duration,
        )

        log.info(f"✓ Done: {len(segments_out)} segments, {len(unique_spks)} speakers")
        return JSONResponse({
            "text": "\n".join(text_lines),
            "language": language or "vi",
            "segments": segments_out,
            "cluster_embeddings": cluster_embeddings,
        })

    except Exception as e:
        log.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try: os.unlink(tmp_path)
        except: pass


# ════════════════════════════════════════════════════════════════════
# CELL 4 — Cloudflare tunnel (free, anonymous, URL tự sinh)
#
# Nếu muốn URL cố định (custom domain), dùng Cloudflare Zero Trust:
#   https://one.dash.cloudflare.com/ → Tunnels → Create tunnel
# ════════════════════════════════════════════════════════════════════

# import subprocess, time, re, threading
#
# # Download cloudflared
# !curl -sL -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
# !chmod +x cloudflared
#
# # Start tunnel in background, scrape the public URL
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
# if PUBLIC_URL:
#     print("\n" + "="*60)
#     print(f"✅ PUBLIC URL: {PUBLIC_URL}")
#     print(f"\nCopy vào benchmarks/.env:")
#     print(f"  PHOWHISPER_BASE_URL={PUBLIC_URL}")
#     print("="*60)
# else:
#     print("⚠ Không tìm thấy URL — check output phía trên")


# ════════════════════════════════════════════════════════════════════
# CELL 5 — Start server (chạy cell này cuối cùng)
#
# Server chạy trong background thread để Jupyter cell trả về ngay.
# Giữ tab Kaggle mở để kernel không bị idle kick.
# ════════════════════════════════════════════════════════════════════

# import uvicorn, threading, asyncio
#
# config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
# server = uvicorn.Server(config)
#
# def _run():
#     loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(loop)
#     loop.run_until_complete(server.serve())
#
# t = threading.Thread(target=_run, daemon=True)
# t.start()
#
# print("✓ Server running on http://0.0.0.0:8000")
# print("✓ Keepalive: cell below giữ kernel sống")


# ════════════════════════════════════════════════════════════════════
# CELL 6 — Keepalive (chống idle kick, chạy sau cell 5)
# ════════════════════════════════════════════════════════════════════

# import time
# while True:
#     print(f"[keepalive] {time.strftime('%H:%M:%S')} — kernel alive", flush=True)
#     time.sleep(240)   # mỗi 4 phút < Kaggle 9-min idle threshold
