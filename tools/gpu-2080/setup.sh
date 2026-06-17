#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# One-time environment setup on nhihb-gpu-2080 (AFTER the NVIDIA driver works).
# Run ON the 2080 box, from ~/stt-server (where this repo's tools/gpu-2080/
# files have been scp'd).
#
# Precondition: `nvidia-smi` shows the RTX 2080 Ti (run install_driver.sh first,
# only with mentor approval).
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Checking GPU driver"
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "!! nvidia-smi not found. Install the driver first (install_driver.sh)."
    exit 1
fi
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

echo "==> Creating venv (.venv)"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip

echo "==> Installing torch + torchaudio (CUDA 12.1 build) FIRST"
# 2080 Ti (Turing) is fully supported by cu121 wheels. If the box's driver is
# older (CUDA 11.8), switch the index URL to .../whl/cu118.
# PIN the version: a bare `pip install torch` grabs a cu130 build that the 535
# driver (CUDA 12.2) can't run. 2.4.1 = verified-working with numpy 2 + pyannote.
pip install torch==2.4.1 torchaudio==2.4.1 --index-url https://download.pytorch.org/whl/cu121

echo "==> Installing server deps"
pip install -r requirements.txt

echo "==> Verifying CUDA visible to torch"
python -c "import torch; assert torch.cuda.is_available(), 'CUDA not visible to torch'; \
print('CUDA OK:', torch.cuda.get_device_name(0))"

echo
echo "✓ Environment ready. Next:"
echo "  export HF_TOKEN=hf_xxxx          # pyannote ToS accepted"
echo "  export SERVER_TOKEN=\$(openssl rand -hex 24)   # optional, save it"
echo "  ./run.sh                         # or install the systemd unit"
