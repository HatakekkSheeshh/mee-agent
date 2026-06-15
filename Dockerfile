# ─── Stage 1: build the React SPA ───────────────────────────────────
# Produces meeting_frontend_react/dist, served by FastAPI (_mount_spa) at /.
FROM node:20-slim AS frontend
WORKDIR /fe
COPY meeting_frontend_react/package.json meeting_frontend_react/package-lock.json ./
# Longer fetch timeout + retries: the build runs behind a slow/corp network where
# the default 5min npm timeout trips ETIMEDOUT on a cold cache.
RUN npm ci --fetch-timeout=600000 --fetch-retries=5
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

COPY start.sh .
RUN mkdir -p output && chmod +x start.sh

EXPOSE 8080

# start.sh picks the role from MEE_ROLE: web (default, API only) or worker
# (Celery worker + API on :8080 for the health check). See start.sh.
CMD ["bash", "start.sh"]
