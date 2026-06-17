"""Kaggle GPU pyannote server — paste each block into a new cell.

Setup instructions:
  1. New Kaggle notebook → settings: Accelerator = GPU T4 x2, Internet ON
  2. Add to "Add-ons → Secrets":
        HF_TOKEN        — your HuggingFace token (accepted pyannote ToS)
        SERVER_TOKEN    — random string for auth (eg. `openssl rand -hex 24`)
        CF_TUNNEL_TOKEN — Cloudflare tunnel token (see tunnel setup below)
  3. Paste cells 1→5 in order, run each
  4. Cell 5 prints the public URL — copy into your local `.env`:
        PYANNOTE_REMOTE_URL=https://<your-tunnel>.cfargotunnel.com
        PYANNOTE_REMOTE_TOKEN=<same as SERVER_TOKEN above>
  5. Restart your Mee backend → uploads now diarize on GPU
  6. Keep the Kaggle tab open; the keepalive (cell 6) prevents idle kick

Cloudflare tunnel — get a stable URL (better than ngrok's volatile sessions):
  - https://one.dash.cloudflare.com/ → Zero Trust → Networks → Tunnels
  - Create tunnel → Copy install command → grab the long token string after `--token`
  - Public hostname: `pyannote-<yourname>.yourdomain.com` → service `http://localhost:8000`
  - No domain? Use the auto-generated `*.trycloudflare.com` URL (free, anonymous)

If you don't want Cloudflare, use ngrok (cell 5b) — easier but URL rotates each run.

Kernel will die after 12h max OR ~9-30 min idle. The keepalive cell prints a
heartbeat every 4 minutes; that alone is enough to stop the idle kick.
"""

# ════════════════════════════════════════════════════════════════════
# CELL 1 — install deps (~3 min first run, cached after)
# ════════════════════════════════════════════════════════════════════

# !pip install -q "pyannote.audio>=3.1" "torchaudio" "soundfile" "fastapi" \
#   "uvicorn[standard]" "python-multipart" "pyngrok"

# ════════════════════════════════════════════════════════════════════
# CELL 2 — load pyannote on GPU
# ════════════════════════════════════════════════════════════════════

import os
import torch

# Secrets resolution — try Kaggle UserSecretsClient first (notebook context),
# fall back to env vars (Docker / AgentBase Endpoint / local dev). Same file
# runs on both surfaces without edits.
try:
    from kaggle_secrets import UserSecretsClient  # type: ignore
    _secrets = UserSecretsClient()
    HF_TOKEN = _secrets.get_secret("HF_TOKEN")
    SERVER_TOKEN = _secrets.get_secret("SERVER_TOKEN")
except Exception:
    HF_TOKEN = os.environ.get("HF_TOKEN", "")
    SERVER_TOKEN = os.environ.get("SERVER_TOKEN", "")
    if not HF_TOKEN:
        raise RuntimeError(
            "HF_TOKEN required (env var or Kaggle secret). Pyannote model "
            "download needs a HuggingFace token that has accepted ToS at "
            "pyannote/speaker-diarization-3.1 + wespeaker-voxceleb-resnet34-LM."
        )
    if not SERVER_TOKEN:
        raise RuntimeError(
            "SERVER_TOKEN required (env var or Kaggle secret). Random string "
            "the /diarize endpoint requires in Authorization: Bearer <token>."
        )

print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "—")

from pyannote.audio import Pipeline, Inference, Model

pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1", use_auth_token=HF_TOKEN,
)
if torch.cuda.is_available():
    pipeline.to(torch.device("cuda"))

emb_model = Model.from_pretrained(
    "pyannote/wespeaker-voxceleb-resnet34-LM", use_auth_token=HF_TOKEN,
)
if torch.cuda.is_available():
    emb_model.to(torch.device("cuda"))
embedder = Inference(emb_model, window="whole")

print("Pyannote loaded ✓")

# ════════════════════════════════════════════════════════════════════
# CELL 3 — FastAPI server with the same diarize output shape as
#          meeting/services/local_diarize.py (drop-in remote)
# ════════════════════════════════════════════════════════════════════

import io
import base64
import tempfile
import logging
from typing import Optional
import numpy as np
import soundfile as sf
from fastapi import FastAPI, UploadFile, Header, HTTPException, File
from pyannote.core import Segment

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pyannote-server")

app = FastAPI()


def _normalize_speaker(spk) -> str:
    s = str(spk).strip()
    if s.startswith("SPEAKER_"):
        return s
    try:
        return f"SPEAKER_{int(s):02d}"
    except ValueError:
        return s


def _health_payload():
    return {"status": "ok", "gpu": torch.cuda.is_available()}


# Two routes for the same payload — `/` for casual probes (curl, browser),
# `/health` for orchestrators that mandate that path (VNG AgentBase Agent
# Runtime, k8s liveness/readiness, AWS ALB target group, etc.).
@app.get("/")
def root():
    return _health_payload()


@app.get("/health")
def health():
    return _health_payload()


@app.post("/diarize")
async def diarize(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    if authorization != f"Bearer {SERVER_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid token")

    audio_bytes = await file.read()
    log.info(f"received {len(audio_bytes) // 1024}KB audio")

    # Decode with soundfile (handles WAV/FLAC/OGG natively). If it fails
    # for codecs like m4a/opus, the caller should pre-transcode via ffmpeg
    # — same contract as the local pyannote service.
    audio_np, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    if audio_np.ndim == 2:
        audio_np = audio_np.T  # (channels, time)
    else:
        audio_np = audio_np[np.newaxis, :]
    waveform = torch.from_numpy(np.ascontiguousarray(audio_np))
    audio_input = {"waveform": waveform, "sample_rate": int(sr)}

    log.info(f"diarizing {waveform.shape[1] / sr:.0f}s audio on GPU…")
    output = pipeline(audio_input)

    # pyannote 4 returns DiarizeOutput with .speaker_diarization;
    # pyannote 3 returns the Annotation directly.
    diarization = getattr(output, "speaker_diarization", output)

    turns = []
    for turn, _, spk in diarization.itertracks(yield_label=True):
        turns.append({
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": _normalize_speaker(spk),
        })
    log.info(f"got {len(turns)} turns, {len(set(t['speaker'] for t in turns))} speakers")

    # Per-cluster embedding. Prefer builtin if pyannote 4 provided it.
    cluster_embeddings = {}
    builtin = getattr(output, "embeddings", None)
    if builtin:
        for spk, emb in builtin.items():
            if hasattr(emb, "numpy"):
                emb = emb.numpy()
            cluster_embeddings[_normalize_speaker(spk)] = emb.flatten().tolist()

    if not cluster_embeddings:
        # Manual: longest turn per cluster → embed.
        for spk in set(t["speaker"] for t in turns):
            spk_turns = [t for t in turns if t["speaker"] == spk]
            best = max(spk_turns, key=lambda t: t["end"] - t["start"])
            seg_end = min(best["end"], best["start"] + 10.0)
            try:
                emb = embedder.crop(audio_input, Segment(best["start"], seg_end))
                if hasattr(emb, "numpy"):
                    emb = emb.numpy()
                cluster_embeddings[spk] = emb.flatten().tolist()
            except Exception as e:
                log.warning(f"embedding {spk} failed: {e}")

    # Per-speaker 3s sample clips. Same logic as local_diarize.
    sample_audio_b64 = {}
    try:
        mono = audio_np[0] if audio_np.ndim == 2 else audio_np
        for spk in set(t["speaker"] for t in turns):
            spk_turns = [t for t in turns if t["speaker"] == spk]
            best = max(spk_turns, key=lambda t: t["end"] - t["start"])
            mid = (best["start"] + best["end"]) / 2.0
            start = max(0.0, mid - 1.5)
            end = min(best["end"], start + 3.0)
            start_i = int(start * sr)
            end_i = int(end * sr)
            if end_i <= start_i:
                continue
            clip = mono[start_i:end_i]
            buf = io.BytesIO()
            sf.write(buf, clip, sr, format="WAV", subtype="PCM_16")
            sample_audio_b64[spk] = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.warning(f"sample extraction failed: {e}")

    return {
        "turns": turns,
        "cluster_embeddings": cluster_embeddings,
        "sample_audio_b64": sample_audio_b64,
    }


# ════════════════════════════════════════════════════════════════════
# CELL 4 — Cloudflare tunnel (stable URL, no signup required)
# ════════════════════════════════════════════════════════════════════
#
# Option A — Quick `trycloudflare.com` (free, anonymous, URL random):

# !curl -L --output cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
# !chmod +x cloudflared

# import subprocess, time, re
# tunnel_proc = subprocess.Popen(
#     ["./cloudflared", "tunnel", "--url", "http://localhost:8000"],
#     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
# )
# # Scrape the URL from stderr; cloudflared prints it within ~5s.
# import time
# for _ in range(20):
#     line = tunnel_proc.stdout.readline()
#     if not line:
#         time.sleep(0.5)
#         continue
#     print(line, end="")
#     m = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
#     if m:
#         PUBLIC_URL = m.group(1)
#         print(f"\n=== PUBLIC URL: {PUBLIC_URL} ===\n"
#               f"In your local .env:\n"
#               f"PYANNOTE_REMOTE_URL={PUBLIC_URL}\n"
#               f"PYANNOTE_REMOTE_TOKEN=<your SERVER_TOKEN>\n")
#         break

# Option B — ngrok fallback if you prefer:

# from pyngrok import ngrok
# NGROK_AUTHTOKEN = secrets.get_secret("NGROK_AUTHTOKEN")
# ngrok.set_auth_token(NGROK_AUTHTOKEN)
# PUBLIC_URL = ngrok.connect(8000, "http").public_url
# print(f"PYANNOTE_REMOTE_URL={PUBLIC_URL}")


# ════════════════════════════════════════════════════════════════════
# CELL 5 — start server in background thread (cell returns ~1s, server runs)
# ════════════════════════════════════════════════════════════════════
#
# Why background thread:
#   Jupyter already has a running asyncio loop. `uvicorn.run()` calls
#   asyncio.run() internally → "cannot be called from a running event loop".
#   Even nest_asyncio doesn't help with newer uvicorn versions.
#   Solution: spawn a thread with its own fresh loop. Cell returns
#   immediately, server keeps serving until the kernel dies.

# import uvicorn, threading, asyncio
#
# config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
# server = uvicorn.Server(config)
#
# def _run_server():
#     loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(loop)
#     loop.run_until_complete(server.serve())
#
# server_thread = threading.Thread(target=_run_server, daemon=True)
# server_thread.start()
#
# print("✓ Server running on http://0.0.0.0:8000 (background thread)")
# print(f"✓ Tunnel URL: {PUBLIC_URL}")
# print("✓ Cell is FREE — keep the Kaggle tab open to prevent idle kick")


# ════════════════════════════════════════════════════════════════════
# CELL 6 — keepalive (run in PARALLEL kernel, prevents idle kick)
# Open a 2nd notebook OR put this above uvicorn.run with nest_asyncio.
# ════════════════════════════════════════════════════════════════════

# import asyncio, time
# async def heartbeat():
#     while True:
#         print(f"[heartbeat] {time.strftime('%H:%M:%S')} — kernel alive", flush=True)
#         await asyncio.sleep(240)  # every 4 minutes < Kaggle's 9-min idle cap
# # If using nest_asyncio in same cell as uvicorn, this won't run until uvicorn yields.
# # Easier: open a 2nd Kaggle notebook session just for keepalive.
