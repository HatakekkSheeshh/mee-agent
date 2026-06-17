# Self-hosting STT + diarization on `nhihb-gpu-2080`

Unified server hosting **pyannote diarization** (resident) + **faster-whisper**
and **PhoWhisper** STT (on-demand, one in VRAM at a time) on an RTX 2080 Ti
(11GB) KVM VM, reached from the dev box over an SSH tunnel.

> **Status (2026-06-15):** files prepared, **not yet deployed.** The box has the
> GPU passed through but **no NVIDIA driver installed yet** — driver install is
> gated on mentor/IT approval (company VM, SSH-only, no console). Everything
> below Phase 0 is ready to run once the driver is in.

## Box facts (verified 2026-06-15)

| | |
|---|---|
| Host alias | `nhihb-gpu-2080` (SSH config already set) |
| Virt / OS | KVM VM, Ubuntu 22.04.5, kernel 5.15.0-177-generic |
| CPU / RAM / disk | 4 vCPU / 15 GB / 83 GB free |
| GPU | RTX 2080 Ti, passed through (PCI `0x10de:0x1e04`), ~11 GB VRAM |
| Driver | **none** — `nvidia-smi` missing, nouveau loaded, `/dev/nvidia*` absent |
| Secure Boot | OFF (UEFI, no SecureBoot efivar) → unsigned modules load fine |
| sudo | passwordless |
| Access | SSH only — **no physical/console** (drives the no-reboot driver approach) |

## VRAM budget (why on-demand)

```
pyannote 3.1 (diarize + wespeaker embed)   ~1.5–2 GB   RESIDENT
faster-whisper large-v3 (fp16, CTranslate2) ~6 GB      } only ONE of these
PhoWhisper-large (HF transformers, fp16)    ~5–6 GB     } in VRAM at a time
```
Both large STT models + pyannote at once ≈ 13–14 GB > 11 GB → won't fit. The
server keeps a single STT slot and swaps backends per request (`model` field).
First request after a swap reloads (~15–30 s); same-backend requests are warm.

## Files

| File | Role |
|---|---|
| `mee_stt_server.py` | The unified FastAPI server (OpenAI-compatible) |
| `requirements.txt` | Python deps (install torch from CUDA index FIRST) |
| `install_driver.sh` | **Mentor-gated.** No-reboot NVIDIA driver install |
| `setup.sh` | venv + torch + deps (after driver works) |
| `run.sh` | Run server (foreground / in tmux) |
| `systemd/mee-stt.service` | Persistent service (survives reboot) |
| `tunnel.sh` | SSH tunnel from dev box → `localhost:9100` |

---

## Phase 0 — NVIDIA driver (⚠️ ASK MENTOR FIRST)

The GPU is present but undriven. `install_driver.sh` installs
`nvidia-driver-535-server` + headers, blacklists nouveau, and `modprobe`s the
driver **live (no reboot)** so we never risk a boot hang we can't recover from
(SSH-only box). Do **not** run it until the mentor confirms it's OK to install a
driver on this VM (there may be a managed/standard version).

```bash
scp -r tools/gpu-2080 nhihb-gpu-2080:~/stt-server
ssh nhihb-gpu-2080
cd ~/stt-server && ./install_driver.sh      # only after approval
nvidia-smi                                    # should list RTX 2080 Ti
```

## Phase 1 — Environment

```bash
ssh nhihb-gpu-2080
cd ~/stt-server
./setup.sh        # venv + torch(cu121) + requirements; verifies torch sees CUDA
```

## Phase 2 — HF token

pyannote needs a HuggingFace token that has accepted ToS on:
`pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`,
`pyannote/wespeaker-voxceleb-resnet34-LM`.

The dev box `.env` is permission-blocked from this agent, so copy the token over
yourself (one `!`-prefixed line in the session, or paste it on the box):

```bash
# on the 2080 box
echo 'export HF_TOKEN=hf_xxxxxxxx' >> ~/.bashrc && source ~/.bashrc
```

## Phase 3 — Run

```bash
# quick test (tmux so it survives logout)
tmux new -s stt
export HF_TOKEN=hf_xxxx
export SERVER_TOKEN=$(openssl rand -hex 24)   # SAVE this — needed on the dev side
./run.sh
# Ctrl+B then D to detach

# or persistent service
sudo cp systemd/mee-stt.service /etc/systemd/system/
sudo nano /etc/systemd/system/mee-stt.service   # fill USER + HF_TOKEN + SERVER_TOKEN
sudo systemctl daemon-reload && sudo systemctl enable --now mee-stt
journalctl -u mee-stt -f
```

## Phase 4 — Tunnel + wire the dev box

```bash
# on the dev box
tools/gpu-2080/tunnel.sh start     # localhost:9100 → 2080:9100 (autossh if installed)
tools/gpu-2080/tunnel.sh status    # hits /health
```

Then in the dev box `.env` (both Mee STT profiles point at the one server):

```env
FASTER_WHISPER_BASE_URL=http://localhost:9100
FASTER_WHISPER_API_KEY=<SERVER_TOKEN>
FASTER_WHISPER_MODEL=faster-whisper

PHOWHISPER_BASE_URL=http://localhost:9100
PHOWHISPER_API_KEY=<SERVER_TOKEN>
PHOWHISPER_MODEL=phowhisper
```

> The Mee `model_registry.py` appends `/v1/audio/transcriptions` — check whether
> your client passes `BASE_URL` with or without `/v1`. The benchmark clients
> append `/v1/audio/transcriptions`, so they want `…:9100` (no `/v1`).

Restart the Mee backend, upload a clip, confirm the transcript has `SPEAKER_NN`
labels and isn't stuck on a processing banner.

## Health check shape

```bash
curl localhost:9100/health
# {"status":"ok","device":"cuda","gpu":"NVIDIA GeForce RTX 2080 Ti",
#  "enabled_backends":["faster_whisper","phowhisper"],"resident_stt":null,...}
```

---

## Re-running the STT benchmark

The benchmark harness lives in `benchmarks/` (pluggable `clients/`). Self-host
clients are wired:

- `benchmarks/clients/faster_whisper.py` → `FasterWhisperClient` (sends `model=faster-whisper`)
- `benchmarks/clients/phowhisper.py` → `PhoWhisperClient` (sends `model=phowhisper`)

Both hit the same tunneled server. In `benchmarks/.env`:

```env
FASTER_WHISPER_BASE_URL=http://localhost:9100
PHOWHISPER_BASE_URL=http://localhost:9100
# FASTER_WHISPER_API_KEY / PHOWHISPER_API_KEY = <SERVER_TOKEN> if auth is on
```

Run from repo root (resumable — skips done rows in `results/raw.csv`):

```bash
python benchmarks/run.py
python benchmarks/analyze.py        # → results/REPORT.md + charts/
```

**Fairness for the self-host pass:** the on-demand server swaps backends, which
fragments VRAM and skews RTF. For clean numbers, run each backend in its own
server process — e.g. `STT_BACKENDS=faster_whisper PRELOAD_STT=faster_whisper ./run.sh`
for the faster-whisper pass, then restart with `STT_BACKENDS=phowhisper` for the
PhoWhisper pass. The runner's resume (skip-done) lets you do this in two passes.

Drop audio in `benchmarks/audio/<domain>/` with matching golden transcripts in
`benchmarks/golden/<domain>/<stem>.txt` (the existing VIVOS + meeting golden
files are already there; audio dirs are gitignored — re-populate them).
