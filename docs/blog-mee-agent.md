# Mee Agent — Từ nỗi đau họp không biên bản đến AI ghi chép tự động, host dễ dàng cho cả team

> **"Họp xong nhớ gì làm nấy"** — câu cửa miệng quen thuộc, nhưng rồi action item thất lạc, deadline trôi qua, và vòng lặp họp để nhắc lại những gì đã họp lần trước cứ thế tiếp diễn.

Mee Agent ra đời để phá vỡ vòng lặp đó.

---

## Phần 1 — Vì sao mình xây dựng Mee Agent?

### Bài toán thật từ thực tế

Mỗi tuần team mình có hàng chục buổi họp: review sản phẩm, sync kỹ thuật, align với stakeholder. Sau mỗi buổi, ai đó phải mở Notion hoặc Google Doc, ngồi gõ lại từ đầu:

- Ai tham dự?
- Đã bàn những gì?
- Ai làm gì, deadline khi nào?

Người ghi biên bản vừa phải theo dõi nội dung họp, vừa phải gõ — kết quả là hoặc thiếu nội dung, hoặc action item ghi nhầm PIC, hoặc đơn giản là... không có biên bản nào cả.

### Ý tưởng: để AI nghe và ghi thay

Mình bắt đầu với câu hỏi đơn giản: *"Liệu AI có thể ngồi trong cuộc họp, nghe, và tự ra biên bản chuẩn template không?"*

Câu trả lời là có — và đây là cách Mee được xây dựng.

---

## Phần 2 — Mee được xây như thế nào?

### Kiến trúc 3 lớp

```
🎙️ Audio (microphone / file upload)
        ↓
🔤 Transcription  — GreenNode MaaS Whisper Large-v3 (real-time via WebSocket)
        ↓
🤖 MoM Generation — Gemini 2.5 Flash phân tích transcript → điền đúng template biên bản
        ↓
📄 Output          — Markdown (.md) + PDF export
```

**Tại sao GreenNode MaaS Whisper?**

- Không cần GPU local — chạy trên máy bình thường
- Hỗ trợ tiếng Việt tốt, giữ nguyên thuật ngữ kỹ thuật tiếng Anh lẫn trong câu tiếng Việt
- Latency thấp đủ để hiển thị transcript real-time trong lúc họp

**Tại sao Gemini 2.5 Flash?**

Transcript thô từ Whisper là một đoạn văn liên tục, không có cấu trúc. Gemini 2.5 Flash có khả năng đọc hiểu ngữ cảnh, nhận biết ai đang nói gì, tách agenda, và quan trọng nhất — **tự trích xuất action item kèm PIC và deadline** mà không cần mình phải đánh dấu thủ công. Cả Whisper lẫn Gemini đều được gọi qua GreenNode MaaS — một endpoint thống nhất, không cần quản lý nhiều key từ nhiều nhà cung cấp khác nhau.

### Tech stack

| Layer | Công nghệ |
|---|---|
| Backend | Python · FastAPI · uvicorn |
| Transcription | WebSocket · GreenNode MaaS Whisper Large-v3 |
| MoM Generation | GreenNode MaaS · Gemini 2.5 Flash (OpenAI-compatible API) |
| MoM Generation | Claude AI (Anthropic) |
| Frontend | Vanilla HTML/CSS/JS · Web Audio API · AudioWorklet |
| Deployment | Docker · GreenNode AgentBase |

---

## Phần 3 — Hướng dẫn sử dụng Mee Agent

### Giao diện tổng quan

Mee có giao diện chia đôi màn hình:

- **Trái:** Sidebar nhập thông tin họp + điều khiển ghi âm
- **Phải:** Transcript real-time (trái) và Biên bản họp (phải)

### Bước 1 — Nhập thông tin cuộc họp

Trước khi bắt đầu ghi âm, điền vào sidebar:

| Trường | Ý nghĩa |
|---|---|
| Tên Project | Map với Redmine project name |
| Mục đích | Purpose of meeting |
| Ngày họp | Tự điền ngày hiện tại |
| Người chủ trì / Thư ký | Tên người điều hành và ghi chép |
| Thành viên | Danh sách người tham dự |

> **Tip:** Tên Project điền đúng sẽ giúp file biên bản được đặt tên chuẩn và dễ tìm lại sau.

### Bước 2 — Chọn chủ đề họp (Vocabulary Hints)

Đây là tính năng ít được để ý nhưng cực kỳ quan trọng với các team kỹ thuật.

Mee tích hợp sẵn từ điển thuật ngữ cho **18 product của GreenNode**. Khi chọn đúng chủ đề, Whisper sẽ nhận diện chính xác các từ kỹ thuật thay vì phiên âm sai.

**Ví dụ thực tế:**
- Không chọn vocab → Whisper nghe "VKS" thành "vê ca ét" hoặc "BCS"
- Chọn `VKS — Kubernetes Service` → nhận diện đúng: cluster, pod, HPA, kubeconfig, Helm chart

Mee hỗ trợ **chọn nhiều product cùng lúc** — hints tự động merge và dedup, không bị trùng lặp.

| # | Product |
|---|---|
| 1 | VKS — Kubernetes Service |
| 2 | vServer — Virtual Server |
| 3 | vStorage — Object & File Storage |
| 4 | VDB — Database as a Service |
| 5 | AI Stack / AI Platform |
| 6 | vCDN · vNetwork · vDNS · vWAF |
| 7 | IAM · KMS · vMonitor Platform |
| 8 | DataSync · Backup & DR · vColo · vCloudStack |
| ... | và nhiều hơn nữa |

### Bước 3 — Dạy Mee học từ mới

Mỗi team có thuật ngữ nội bộ riêng. Mee có phần **"Mee đã học"** cho phép bạn thêm cặp sửa lỗi:

```
Mee nghe sai: "deplore"  →  Đúng là: "deploy"
Mee nghe sai: "AgentBased"  →  Đúng là: "AgentBase"
```

Những corrections này được lưu lại vào vocab pool, áp dụng cho tất cả các phiên sau — không cần nhập lại.

### Bước 4 — Ghi âm hoặc Upload

**Ghi âm trực tiếp:**

1. Nhấn **▶ Ghi âm** — Mee kết nối microphone và WebSocket tới Whisper server
2. Transcript xuất hiện real-time, có timestamp từng đoạn
3. Nhấn **■ Dừng** khi họp kết thúc

**Upload file audio:**

Có sẵn file ghi âm buổi họp (`.wav`, `.mp3`, `.m4a`, `.webm`)? Nhấn **↑ Upload Audio** — Mee transcribe xong rồi mới cho phép tạo biên bản.

**Nhập transcript thủ công:**

Dùng nút **✏️ Nhập tay** nếu bạn đã có transcript từ nguồn khác hoặc muốn paste nội dung chỉnh sửa.

### Bước 5 — Tạo biên bản họp (MoM)

Sau khi có transcript, nhấn **✦ Tạo MoM**.

Claude AI sẽ đọc toàn bộ transcript và tự động điền vào template chuẩn:

```markdown
# MINUTES OF MEETING (MoM)

| Mục đích cuộc họp | ... |
| Ngày họp          | ... |
| Người chủ trì     | ... |

## THÀNH PHẦN THAM GIA
| No. | Họ và tên | Đơn vị | Chức vụ |

## NỘI DUNG CUỘC HỌP
| Topic No. | Tóm tắt | Chi tiết |

## Next step: Các công việc tiếp theo
| PIC | Ngày | Nội dung |
```

Quá trình tạo mất khoảng **30–60 giây** tùy độ dài buổi họp.

### Bước 6 — Chỉnh sửa action items

Action items hiển thị dưới dạng **ô có thể chỉnh sửa trực tiếp** — click vào PIC, nội dung, hoặc deadline để sửa ngay mà không cần mở file.

### Bước 7 — Export

| Nút | Chức năng |
|---|---|
| **↓ Tải .md** | Download file Markdown — paste ngay vào Notion, Confluence, GitHub |
| **↓ Xuất PDF** | In hoặc lưu PDF qua trình duyệt |
| **📋 Copy Tasks JSON** | Copy JSON danh sách action items để gửi sang tool khác |

---

## Phần 4 — Host trên GreenNode AgentBase, share cho cả team dùng

### Vấn đề khi chạy local

Mee chạy local hoạt động tốt, nhưng mỗi người phải tự cài Python, tự config `.env`, tự chạy server. Không thực tế để share cho cả team.

### Giải pháp: GreenNode AgentBase

[GreenNode AgentBase](https://agentbase.greennode.vn) là nền tảng cho phép deploy và host AI agent với vài bước đơn giản — không cần quản lý server, không cần DevOps.

Với Mee, sau khi deploy lên AgentBase:

- Team nhận **một URL duy nhất** để truy cập — mở trình duyệt là dùng được
- Không cần cài đặt gì thêm
- Nhiều người dùng cùng lúc, mỗi phiên độc lập
- Mee hoạt động sau proxy AgentBase hoàn toàn trong suốt — API paths tự adapt

**Deploy chỉ cần:**

```bash
# 1. Cấu hình .greennode.json
# 2. Chạy deploy script
./deploy.sh
```

AgentBase tự lo container, routing, scaling. Mình chỉ cần lo logic của agent.

> Đây là lý do mình chọn AgentBase thay vì tự dựng infrastructure: **tập trung vào xây agent, không phải vận hành server**.

---

## Kết

Mee Agent giải quyết được điều mà mình nghĩ đơn giản nhưng mãi không ai làm: **để AI ngồi họp cùng, ghi chép thay, trả về biên bản đúng chuẩn — không cần nhớ, không cần gõ, không cần chỉnh nhiều**.

Từ ý tưởng đến bản chạy thực tế trên cả team chỉ mất vài tuần, phần lớn nhờ stack đã có sẵn (GreenNode MaaS + Gemini 2.5 Flash + AgentBase) và không phải tự dựng infrastructure từ đầu.

---

### 📌 Bạn muốn thử Mee Agent?

Liên hệ **huyenttn3** để được cấp quyền truy cập và trải nghiệm trực tiếp — đừng để action item tiếp theo của bạn rơi vào quên lãng.

---

### 🔮 Sắp ra mắt

Mee Agent đang được nâng cấp để **tự động tạo task trực tiếp lên tool quản lý task nội bộ** — từ action item trong biên bản thành ticket thật, không cần copy-paste thủ công. Stay tuned.

---

*Mee Agent · Powered by GreenNode MaaS (Whisper + Gemini 2.5 Flash) · Hosted on GreenNode AgentBase*
