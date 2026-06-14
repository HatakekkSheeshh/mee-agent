# ─── Stage 1: build the React SPA ───────────────────────────────────
# Produces meeting_frontend_react/dist, served by FastAPI (_mount_spa) at /.
FROM node:20-slim AS frontend
WORKDIR /fe
COPY meeting_frontend_react/package.json meeting_frontend_react/package-lock.json ./
RUN npm ci
COPY meeting_frontend_react/ ./
RUN npm run build

# ─── Stage 2: python runtime (single port 8080) ─────────────────────
FROM python:3.11-slim

WORKDIR /app

# ffmpeg: audio decode/resample for /api/transcribe chunking + voiceprint.
# libsndfile1: soundfile.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY meeting/ ./meeting/
COPY whisper_live/ ./whisper_live/
COPY meeting_frontend/ ./meeting_frontend/
COPY main.py .

# Built React SPA from stage 1 → FastAPI serves it at / (with SPA fallback).
COPY --from=frontend /fe/dist ./meeting_frontend_react/dist

RUN mkdir -p output

EXPOSE 8080

# main.py runs the full FastAPI app (HTTP + /ws live transcription) on :8080.
CMD ["python", "main.py"]
