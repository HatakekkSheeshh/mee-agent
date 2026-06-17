# Pyannote 3.1 GPU inference server, ~6-7 GB image.
# Build:
#   cd tools/kaggle
#   docker build --no-cache --build-arg HF_TOKEN=hf_xxx -t mee/pyannote:0.1 .
#
# Run local CPU test (no --gpus all):
#   docker run --rm -p 8000:8000 -e SERVER_TOKEN=test -e HF_TOKEN=hf_xxx mee/pyannote:0.1
#
# Run with GPU (production):
#   docker run --gpus all -p 8000:8000 -e SERVER_TOKEN=mysecret mee/pyannote:0.1

FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# Ubuntu 22.04 default python3 is 3.10. Install 3.11 + bootstrap pip explicitly
# FOR python3.11 (the apt python3-pip targets 3.10 and installed packages
# wouldn't be visible to `python3.11`). We DON'T symlink `python` to 3.11 —
# every Python invocation in this Dockerfile uses `python3.11` directly, so
# there's no chance of running the wrong interpreter.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3.11 python3.11-distutils \
        ffmpeg ca-certificates curl \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 \
    && rm -rf /var/lib/apt/lists/* \
    && python3.11 -m pip --version

WORKDIR /app

# Install torch + pyannote + server deps + VERIFY pyannote imports.
# Single RUN keeps the install + verification in one cacheable layer so a
# cache-hit can't deliver "installed but unimportable" state.
RUN python3.11 -m pip install --no-cache-dir \
        torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cu121 \
    && python3.11 -m pip install --no-cache-dir \
        "pyannote.audio>=3.1,<4.0" \
        "huggingface_hub<1.0" \
        "soundfile" \
        "numpy<2.0" \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.27" \
        "python-multipart" \
    && python3.11 -c "import pyannote.audio, huggingface_hub; \
print('pyannote.audio', pyannote.audio.__version__, '✓'); \
print('huggingface_hub', huggingface_hub.__version__, '✓')"

# Pre-download weights to bake into image → no cold-start download. HF_TOKEN
# passed as --build-arg, used here only, not retained in any layer.
ARG HF_TOKEN
RUN test -n "$HF_TOKEN" || (echo "ERROR: --build-arg HF_TOKEN=<your-token> required" && exit 1) \
    && python3.11 -c "from pyannote.audio import Pipeline, Model; \
Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', use_auth_token='${HF_TOKEN}'); \
Model.from_pretrained('pyannote/wespeaker-voxceleb-resnet34-LM', use_auth_token='${HF_TOKEN}'); \
print('weights cached ✓')"

COPY pyannote_server.py /app/pyannote_server.py

# Runtime env (set via endpoint config / docker -e, NOT here):
#   SERVER_TOKEN — bearer token for /diarize auth (required)
#   HF_TOKEN     — only needed if weights re-download (already cached above)
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python3.11 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health').read()" || exit 1

CMD ["python3.11", "-m", "uvicorn", "pyannote_server:app", "--host", "0.0.0.0", "--port", "8080"]
