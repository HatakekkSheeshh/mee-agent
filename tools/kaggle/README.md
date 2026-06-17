# Kaggle GPU pyannote — quick setup

## Mục tiêu

Chạy pyannote 3.1 trên Kaggle T4 GPU (free) thay vì local CPU → diarize file 1h chỉ ~30-60 giây (vs 6-12 phút CPU). Drop-in via env var, fallback tự động khi Kaggle kernel offline.

## Setup lần đầu (15 phút)

### 1. Lấy tokens

| Token | Lấy từ đâu | Dùng cho |
|-------|-----------|----------|
| `HF_TOKEN` | https://huggingface.co/settings/tokens → "Read" token + accept pyannote ToS tại 2 model pages | Pyannote download model weights |
| `SERVER_TOKEN` | Tự gen: `openssl rand -hex 24` | Bảo vệ endpoint (bearer auth) |

Chấp nhận ToS tại:
- https://huggingface.co/pyannote/speaker-diarization-3.1
- https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM

### 2. Tạo Kaggle notebook

1. https://www.kaggle.com/code → "New Notebook"
2. Notebook settings (sidebar phải):
   - **Accelerator**: GPU T4 x2
   - **Internet**: ON
   - **Persistence**: Files only (không cần variables)
3. **Add-ons → Secrets** → Add 2 secrets:
   - `HF_TOKEN` = token bước 1
   - `SERVER_TOKEN` = random string bước 1

### 3. Paste code

Copy nội dung `pyannote_server.py` thành các cell tương ứng (comment `# CELL N` trong file đánh dấu rõ chỗ tách):

| Cell | Nội dung |
|------|----------|
| 1 | `!pip install ...` (uncomment dòng `!pip`) |
| 2 | Import + load pyannote pipeline trên GPU |
| 3 | Định nghĩa FastAPI app với `/diarize` endpoint |
| 4 | Cloudflare tunnel (lấy public URL) — uncomment hết block A |
| 5 | `uvicorn.run(app)` — uncomment + run, sẽ block |

Run lần lượt 1→4. Cell 4 sẽ in URL kiểu:
```
=== PUBLIC URL: https://abc-xyz.trycloudflare.com ===
```

### 4. Wire vào local Mee

Trong `.env` của bạn:

```bash
PYANNOTE_REMOTE_URL=https://abc-xyz.trycloudflare.com
PYANNOTE_REMOTE_TOKEN=<SERVER_TOKEN ở bước 1>
```

Restart backend → `python run_meeting.py` → từ giờ mọi pyannote call đi qua Kaggle GPU.

### 5. Run cell 5 trên Kaggle

`uvicorn.run(...)` — sẽ block + log mỗi request. Để tab Kaggle open.

## Maintenance hàng ngày

**Restart sáng**: Kernel Kaggle die sau 12h hoặc idle ~9-30 phút.

Workflow đơn giản:
1. Sáng mở Kaggle notebook
2. Settings → "Run All" 
3. Cell 4 print URL mới (vì `trycloudflare.com` URL random mỗi run)
4. Copy URL → cập nhật `.env` local + restart backend
5. Cell 5 chạy uvicorn, để tab open

**Tránh idle kick**:
- Mở tab Kaggle song song, scroll/click thỉnh thoảng
- Hoặc dùng browser extension auto-refresh page mỗi 5 phút
- Hoặc paid Kaggle = không bị idle kick

## Verify

Test endpoint trực tiếp:

```bash
# Smoke test
curl -X POST https://<your-tunnel>.trycloudflare.com/diarize \
  -H "Authorization: Bearer $SERVER_TOKEN" \
  -F "file=@/path/to/audio.wav"

# Expected response (JSON):
{
  "turns": [{"start": 0.5, "end": 12.3, "speaker": "SPEAKER_00"}, ...],
  "cluster_embeddings": {"SPEAKER_00": [256 floats], ...},
  "sample_audio_b64": {"SPEAKER_00": "<base64 wav>", ...}
}
```

Log backend khi upload sẽ thấy:
```
[diarize] remote pyannote OK — 145 turns, 5 clusters, 5 samples
```

Khi Kaggle offline:
```
[diarize] remote pyannote failed (...); falling back to local CPU
[local_diarize] running pyannote diarization on 4461.7s audio…
```

## Troubleshooting

| Lỗi | Nguyên nhân | Fix |
|-----|-------------|-----|
| `Connection timeout` | Kaggle kernel đã chết | Restart "Run All" trên Kaggle |
| `401 Unauthorized` | Token mismatch | Verify `PYANNOTE_REMOTE_TOKEN` = `SERVER_TOKEN` trên Kaggle |
| `400 Bad Request` ở `/diarize` | File audio corrupt | Pre-transcode qua ffmpeg trước khi upload |
| Cell 4 không in URL | cloudflared chưa tải xong | Đợi 10s, hoặc check stderr của cell |
| `OutOfMemoryError` trên Kaggle | Audio quá dài (>3h) | Pre-split audio + nhiều requests |
| `403 Forbidden` từ HuggingFace | Chưa accept ToS | Vào 2 model pages, accept |

## Alternatives sau khi quen

| Platform | Cost | Khi nào dùng |
|----------|------|--------------|
| **Kaggle** (this) | $0 | Hackathon, dev |
| **Modal Labs** | $0.000150/sec T4 (~$0.54/h) | Production scale-to-zero |
| **Replicate** | $0.000800/sec | Easiest setup, đắt nhất |
| **Runpod / Vast.ai** | $0.20-0.34/h T4 | 24/7 production cheap |
| **Self-host on VPS GPU** | $30-100/tháng | Full control, không dependency |

Source code `pyannote_server.py` có thể adapt cho mọi platform — chỉ thay phần tunnel.
