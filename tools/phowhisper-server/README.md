# PhoWhisper + pyannote server (L40 deploy)

Self-hosted STT cho tiếng Việt với speaker diarization. OpenAI-compatible API.

## Architecture

```
POST /v1/audio/transcriptions   (multipart with `file`)
        │
        ▼
[PhoWhisper-large]  →  transcript + word/segment timestamps
        │
        ▼
[pyannote 3.1]      →  speaker turns + timestamps
        │
        ▼
[Align overlap]     →  for each ASR segment, pick pyannote speaker với max overlap
        │
        ▼
Response: {text: "SPEAKER_00: ...\nSPEAKER_01: ...", segments: [...]}
```

## Hardware

- NVIDIA GPU 8GB+ VRAM (L40 48GB → comfortable)
- Khoảng disk: 5GB (PhoWhisper ~3GB + pyannote ~500MB)

## Setup trên L40

### 1. SCP folder lên L40

Trên máy local:
```bash
# Từ /home/lap15466/greennode/mee-meeting-agent/tools/
scp -r phowhisper-server user@l40-server:~/
```

### 2. SSH vào L40

```bash
ssh user@l40-server
cd ~/phowhisper-server
```

### 3. Tạo venv + install deps

```bash
python3 -m venv .venv
source .venv/bin/activate

# CUDA 12.1 (L40 driver)
pip install --upgrade pip
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# Còn lại
pip install -r requirements.txt
```

Verify CUDA:
```bash
python -c "import torch; print('CUDA:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
# Expected: CUDA: True | NVIDIA L40
```

### 4. HuggingFace token (BẮT BUỘC cho pyannote)

1. Tạo account [huggingface.co](https://huggingface.co)
2. Accept terms tại 2 model pages:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
3. Tạo read token ở [settings/tokens](https://huggingface.co/settings/tokens)

```bash
echo 'export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxx' >> ~/.bashrc
source ~/.bashrc
```

### 5. Run server

```bash
# Foreground (test trước)
python server.py
# Đợi ~30-60s để download + load models. Thấy "Application startup complete" là OK.
```

Background với tmux:
```bash
tmux new -s phowhisper
cd ~/phowhisper-server && source .venv/bin/activate && python server.py
# Detach: Ctrl+B rồi D
```

Hoặc systemd (production) — xem [section systemd](#systemd-optional) bên dưới.

## Config qua env vars

| Env var | Default | Mô tả |
|---|---|---|
| `HF_TOKEN` | (required) | HuggingFace token để load pyannote |
| `ASR_MODEL` | `vinai/PhoWhisper-large` | Có thể dùng PhoWhisper-medium cho nhanh hơn |
| `DIARIZE_MODEL` | `pyannote/speaker-diarization-3.1` | |
| `PORT` | `9100` | Đổi sang `9101` nếu chạy thêm 1 instance cho live mode |

Live mode (Whisper turbo, nhanh hơn) — set:
```bash
export ASR_MODEL=openai/whisper-large-v3-turbo
export PORT=9101
python server.py
```

## Test

### Health check

```bash
curl http://localhost:9100/health
# Expected: {"status":"ok","device":"cuda","asr_model":"vinai/PhoWhisper-large",...}
```

### Transcribe 1 file

```bash
curl -X POST http://localhost:9100/v1/audio/transcriptions \
     -F "file=@sample.mp3" \
     -F "language=vi" \
     -F "max_speakers=4" | python3 -m json.tool
```

Response:
```json
{
  "text": "SPEAKER_00: Hôm nay mình bàn về deploy v1...\nSPEAKER_01: OK chốt rồi.",
  "language": "vi",
  "segments": [
    {"speaker": "SPEAKER_00", "text": "Hôm nay mình bàn về deploy v1", "start": 0.0, "end": 3.2},
    {"speaker": "SPEAKER_01", "text": "OK chốt rồi", "start": 3.5, "end": 4.8}
  ]
}
```

### Code-switching prompt

```bash
curl -X POST http://localhost:9100/v1/audio/transcriptions \
     -F "file=@meeting.mp3" \
     -F "language=vi" \
     -F "prompt=Cuộc họp về deploy, API, sprint, backend, frontend, database, refactor, code review, pull request, Docker, Kubernetes" \
     -F "max_speakers=5"
```

→ Whisper sẽ bias toward giữ nguyên các từ tech tiếng Anh.

## Network — Reach từ Mee server

### Cách A: Same LAN / public IP

Update `.env` của mee-meeting-agent:
```env
WHISPER_BASE_URL=http://<L40_IP>:9100
WHISPER_API_KEY=not-used-but-keep-something
WHISPER_MODEL=phowhisper
```

### Cách B: SSH tunnel (an toàn nhất)

Trên máy chạy mee:
```bash
ssh -L 9100:localhost:9100 user@l40-server -N -f
```

Update `.env`:
```env
WHISPER_BASE_URL=http://localhost:9100
```

## systemd (optional)

Tạo `/etc/systemd/system/phowhisper.service`:

```ini
[Unit]
Description=PhoWhisper + pyannote server
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/phowhisper-server
Environment="HF_TOKEN=hf_xxxxxxxxxxxxx"
Environment="ASR_MODEL=vinai/PhoWhisper-large"
Environment="PORT=9100"
Environment="PATH=/home/YOUR_USER/phowhisper-server/.venv/bin"
ExecStart=/home/YOUR_USER/phowhisper-server/.venv/bin/python server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable phowhisper
sudo systemctl start phowhisper
sudo systemctl status phowhisper
journalctl -u phowhisper -f   # tail logs
```

## Performance estimate trên L40

| Audio length | PhoWhisper-large + diarize | Notes |
|---|---|---|
| 1 phút | ~5-10s | First call slower (model warmup) |
| 10 phút | ~30-60s | |
| 30 phút | ~2-4 phút | |
| 1 giờ | ~5-10 phút | Recommend chunk thành 30 phút |

## Troubleshooting

| Issue | Fix |
|---|---|
| `HF_TOKEN required` | Set env var + accept pyannote 3.1 + segmentation-3.0 terms ở HF |
| `CUDA out of memory` | Đổi `ASR_MODEL=vinai/PhoWhisper-medium` (smaller) |
| `torchaudio backend not found` | `pip install ffmpeg-python` hoặc `apt install ffmpeg` |
| `Cannot resolve` pyannote model | Token chưa accept terms — vào HF page click Agree |
| Empty `text` | Audio silent / quá ngắn / unsupported format → check `ffprobe sample.mp3` |
| Server load slow | First call download model ~3GB từ HF — bình thường, subsequent calls fast |

## Files

- `server.py` — FastAPI server, OpenAI-compatible
- `requirements.txt` — Python deps (torch installed separately)
- `README.md` — file này

## Switch giữa models trên L40

Khi muốn so sánh PhoWhisper vs Whisper turbo, chạy 2 instance:

```bash
# Terminal 1 — PhoWhisper-large port 9100
tmux new -s phow-large
ASR_MODEL=vinai/PhoWhisper-large PORT=9100 python server.py
# Ctrl+B D

# Terminal 2 — Whisper turbo port 9101 (live mode)
tmux new -s whisper-turbo
ASR_MODEL=openai/whisper-large-v3-turbo PORT=9101 python server.py
```

Mee `.env`:
```env
WHISPER_BASE_URL=http://<L40_IP>:9100      # quality (PhoWhisper)
WHISPER_LIVE_URL=http://<L40_IP>:9101      # speed (turbo)
```

Code Mee sẽ chọn endpoint tùy mode (upload vs live).
