# Mee — Meeting Note Agent

> Ghi âm cuộc họp → Transcript real-time → Biên bản họp (MoM) tự động theo chuẩn GreenNode / VNG Cloud.

---

## Tính năng

- **Ghi âm real-time** qua microphone hoặc upload file audio
- **Transcription** bằng VNGCloud MaaS Whisper API (không cần GPU local)
- **Tự động tạo MoM** (Minutes of Meeting) theo template chuẩn:
  - Thông tin cuộc họp (mục đích, địa điểm, ngày, người chủ trì, thư ký)
  - Bảng thành viên tham gia
  - Nội dung từng agenda item
  - Danh sách action items (PIC + deadline)
  - Tóm tắt tổng quan
- **Vocabulary Hints** tích hợp sẵn 18 product topics của VNG Cloud / GreenNode — chọn product là Whisper nhận diện đúng thuật ngữ kỹ thuật
- **Xuất file Markdown** (`.md`) tải về ngay trên UI

---

## Yêu cầu

- Python 3.10+
- Claude Code CLI (`claude` có trong PATH)
- VNGCloud MaaS API key (cho Whisper transcription)

---

## Cài đặt

```bash
# 1. Clone repo
git clone <repo-url>
cd Meeting-note-agent

# 2. Cài dependencies
pip install fastapi uvicorn websockets python-dotenv soundfile requests numpy

# 3. Tạo file .env
cp .env.example .env
# Điền WHISPER_BASE_URL và WHISPER_API_KEY vào .env
```

**Nội dung `.env`:**
```env
WHISPER_BASE_URL=https://your-vngcloud-maas-endpoint
WHISPER_API_KEY=your-api-key-here
WHISPER_MODEL=openai/whisper-large-v3
```

---

## Chạy Mee

```bash
python run_meeting.py
```

Mở trình duyệt tại **[http://localhost:8001](http://localhost:8001)**

| Server | Port | Vai trò |
|--------|------|---------|
| HTTP | 8001 | Web UI + API |
| WebSocket | 9091 | Real-time audio transcription |

### Tuỳ chọn

```bash
python run_meeting.py --http-port 8001 --ws-port 9091 --maas-url <url> --maas-key <key>
```

---

## Workflow

```
1. Nhập thông tin họp  →  Tiêu đề, mục đích, ngày, người chủ trì, attendees
2. Chọn product topic  →  Vocab hints tự điền (VKS, AI Stack, vServer, ...)
3. Ghi âm / Upload     →  Transcript hiện real-time
4. Tạo biên bản        →  AI (Claude) phân tích transcript → fill MoM
5. Tải về              →  File .md theo đúng chuẩn MoM
```

---

## Cấu trúc project

```
Meeting-note-agent/
├── run_meeting.py              # Entry point
├── .env                        # Config (API keys)
│
├── meeting/                    # Core logic
│   ├── app.py                  # FastAPI endpoints
│   ├── note_generator.py       # Claude CLI → MoM JSON
│   └── report_generator.py     # MoM JSON → Markdown file
│
├── meeting_frontend/           # Web UI
│   ├── index.html
│   ├── style.css
│   ├── app.js                  # Audio capture + UI logic
│   ├── vocab_hints.js          # 18 product topics vocabulary
│   └── audioprocessor.js       # WebAudio worklet
│
├── whisper_live/               # WebSocket transcription engine
└── output/                     # Transcript + MoM files (auto-created)
```

---

## Vocabulary Hints

Mee tích hợp sẵn từ điển thuật ngữ kỹ thuật cho 18 product của VNG Cloud / GreenNode:

| # | Product | Ví dụ từ khoá |
|---|---------|---------------|
| 1 | VKS — Kubernetes Service | cluster, pod, Helm, HPA, kubeconfig |
| 2 | vServer — Virtual Server | VM, flavor, Security Group, Auto Scaling |
| 3 | vStorage — Object & File Storage | Bucket, rclone, S3-compatible, Lifecycle |
| 4 | VDB — Database as a Service | RDS, PostgreSQL, Read Replica, Failover |
| 5 | AI Stack / AI Platform | LLM, RAG, Inference Endpoint, pgvector |
| 6 | vCDN | HLS, Edge Server, Cache TTL, CDN Purge |
| 7 | vNetwork | VPC, ALB, NLB, GSLB, Floating IP |
| 8 | vDNS | DNS Zone, DNSSEC, TTL, A Record |
| 9 | vWAF | DDoS, Rate Limiting, SQL Injection |
| 10 | IAM | Role, Policy, RBAC, MFA, Service Account |
| 11 | KMS | Encryption Key, Key Rotation, Master Key |
| 12 | vMonitor Platform | Metric, Alert Rule, Dashboard, Webhook |
| 13 | DataSync | Transfer Job, rclone, S3-Compatible |
| 14 | Backup Center & DR | RPO, RTO, Failover, Veeam, DRC |
| 15 | vColocation | Rack, Power Meter, Asset Tracking |
| 16 | vCloudStack | Hybrid Cloud, On-Premise, Data Locality |
| 17 | Veka.ai / vCloudCam | Face Recognition, Live Streaming |
| 18 | General | API, IaC, Terraform, SLA, Pay-as-you-go |

Giữ **Ctrl / Cmd** để chọn nhiều product — hints tự động merge và dedup.

---

## Output

File MoM xuất ra tại `output/MoM_<title>_<date>.md`, theo đúng template:

```markdown
# MINUTES OF MEETING (MoM)

| Mục đích | ... |
| Ngày họp | ... |
...

## THÀNH PHẦN THAM GIA
| No. | Họ và tên | Đơn vị | Chức vụ |

## NỘI DUNG CUỘC HỌP
| Topic No. | Tóm tắt | Chi tiết |

## Next step
| PIC | Ngày | Nội dung |
```
