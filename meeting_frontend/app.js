/**
 * Meeting Note Agent — Frontend
 * Handles audio capture, WebSocket transcription, and MoM generation.
 */

// Auto-detect API base path so fetch calls work both locally and behind AgentBase proxy.
// Locally: pathname = '/' → API_BASE = ''
// Behind proxy: pathname = '/runtime/endpoint-xxx' → API_BASE = '/runtime/endpoint-xxx'
const API_BASE = window.location.pathname.replace(/\/$/, '');

let websocket = null;
let audioContext = null;
let audioWorklet = null;
let mediaStream = null;
let sessionId = null;
let timerInterval = null;
let startTime = null;
let allCompletedSegments = [];
let wsReconnectAttempts = 0;
let downloadUrl = null;

// Set today's date as default + init vocab hints dropdown + load vocab pool
document.addEventListener("DOMContentLoaded", () => {
    const today = new Date().toISOString().split("T")[0];
    document.getElementById("meeting-date").value = today;
    initVocabHints();
    loadVocabPool();

    // Enable Generate MoM button when user types/pastes into transcript textarea
    const transcriptEl = document.getElementById("transcript");
    const genBtn = document.getElementById("btn-generate-notes");
    const saveBtn = document.getElementById("btn-save-transcript");
    if (transcriptEl) {
        const updateButtons = () => {
            const hasText = transcriptEl.value.trim().length > 0;
            if (genBtn) genBtn.disabled = !hasText;
            if (saveBtn) saveBtn.disabled = !hasText;
        };
        transcriptEl.addEventListener("input", updateButtons);
        transcriptEl.addEventListener("paste", () => setTimeout(updateButtons, 0));
        updateButtons();  // Initial state
    }
});

// ─── Recording Controls ─────────────────────────────────────────

async function startRecording() {
    const title = document.getElementById("meeting-title").value.trim();
    if (!title) {
        alert("Vui lòng nhập tiêu đề project.");
        return;
    }

    const wsUrl = document.getElementById("ws-url").value.trim();
    const language = document.getElementById("language").value;

    // Reset transcript state for a fresh recording (avoid stale segments from
    // a previous session showing up alongside new ones with reset timestamps)
    allCompletedSegments = [];
    wsReconnectAttempts = 0;
    const transcriptEl = document.getElementById("transcript");
    if (transcriptEl) transcriptEl.value = "";
    const countEl = document.getElementById("segment-count");
    if (countEl) countEl.textContent = "0 đoạn";

    setStatus("Đang kết nối tới server transcription...", "connecting");

    try {
        // Create session
        const resp = await fetch(`${API_BASE}/api/session`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title,
                purpose: document.getElementById("meeting-purpose").value.trim(),
                venue: document.getElementById("meeting-venue").value.trim(),
                date: formatDateVN(document.getElementById("meeting-date").value),
                chaired_by: document.getElementById("chaired-by").value.trim(),
                noted_by: document.getElementById("noted-by").value.trim(),
                attendees: document.getElementById("attendees").value.trim(),
            }),
        });
        const data = await resp.json();
        sessionId = data.session_id;

        // Connect WebSocket
        websocket = new WebSocket(wsUrl);
        websocket.onopen = async () => {
            const vocabHints = document.getElementById("vocab-hints").value.trim();
            const prompt = await fetchWhisperPrompt(vocabHints, language);
            const config = {
                uid: sessionId,
                language,
                task: "transcribe",
                model: "large-v3",
                use_vad: true,
                initial_prompt: prompt,
            };
            websocket.send(JSON.stringify(config));
            setStatus("Đang chờ server...", "connecting");
        };
        websocket.onmessage = (event) => handleServerMessage(JSON.parse(event.data));
        websocket.onerror = () => setStatus("Lỗi kết nối. Đang thử lại...", "error");
        websocket.onclose = () => {
            if (document.getElementById("btn-stop").disabled) {
                setStatus("Đã ngắt kết nối", "idle");
                return;
            }
            stopAudioCapture();
            const delay = Math.min(3000, 1000 * (wsReconnectAttempts + 1));
            wsReconnectAttempts++;
            if (wsReconnectAttempts <= 5) {
                setStatus(`Mất kết nối. Thử lại sau ${delay / 1000}s... (${wsReconnectAttempts}/5)`, "error");
                setTimeout(() => reconnectWebSocket(wsUrl, language), delay);
            } else {
                setStatus("Mất kết nối. Nhấn Dừng rồi Bắt đầu lại.", "error");
            }
        };
    } catch (err) {
        setStatus(`Lỗi: ${err.message}`, "error");
    }
}

function handleServerMessage(msg) {
    if (msg.message === "SERVER_READY") {
        wsReconnectAttempts = 0;
        setStatus("Đang ghi âm...", "recording");
        startAudioCapture();
        startTimer();
        document.getElementById("btn-start").disabled = true;
        document.getElementById("btn-stop").disabled = false;
        document.getElementById("btn-generate-notes").disabled = true;
        return;
    }
    if (msg.message === "DISCONNECT") { stopRecording(); return; }
    if (msg.status === "WAIT") { setStatus(`Server bận. Chờ ~${Math.ceil(msg.message)} phút.`, "connecting"); return; }
    if (msg.segments) updateTranscript(msg.segments);
}

function stopRecording() {
    wsReconnectAttempts = 999;
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        websocket.send(new TextEncoder().encode("END_OF_AUDIO"));
        websocket.close();
    }
    stopAudioCapture();
    // Capture actual recording duration so it can be passed to import-transcript
    // when user later clicks Generate MoM (avoids 00:00 in meta bar).
    if (startTime) {
        window.lastRecordedDurationSec = Math.floor((Date.now() - startTime) / 1000);
    }
    stopTimer();
    setStatus("Đã dừng ghi âm. Bạn có thể tạo biên bản họp.", "idle");
    document.getElementById("btn-start").disabled = false;
    document.getElementById("btn-stop").disabled = true;
    document.getElementById("btn-generate-notes").disabled = false;
    document.getElementById("btn-save-transcript").disabled = false;
}

function reconnectWebSocket(wsUrl, language) {
    websocket = new WebSocket(wsUrl);
    websocket.onopen = async () => {
        const vocabHints = document.getElementById("vocab-hints").value.trim();
        const prompt = await fetchWhisperPrompt(vocabHints, language);
        websocket.send(JSON.stringify({
            uid: sessionId, language, task: "transcribe", model: "large-v3", use_vad: true,
            initial_prompt: prompt,
        }));
    };
    websocket.onmessage = (e) => handleServerMessage(JSON.parse(e.data));
    websocket.onerror = () => {};
    websocket.onclose = () => {
        if (document.getElementById("btn-stop").disabled) return;
        wsReconnectAttempts++;
        if (wsReconnectAttempts <= 5) setTimeout(() => reconnectWebSocket(wsUrl, language), Math.min(3000, wsReconnectAttempts * 1000));
        else setStatus("Mất kết nối. Nhấn Dừng rồi Bắt đầu lại.", "error");
    };
    startAudioCapture();
}

// ─── Audio Capture ──────────────────────────────────────────────

async function startAudioCapture() {
    try {
        // Honor a saved mic preference (set via Audio Devices modal). Falls
        // back to system default if the saved id is invalid/missing.
        const savedMic = localStorage.getItem('mee.audioInputDeviceId') || '';
        const audioConstraints = {
            channelCount: 1,
            sampleRate: 16000,
            echoCancellation: true,
            noiseSuppression: true,
        };
        if (savedMic) audioConstraints.deviceId = { ideal: savedMic };

        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
        audioContext = new AudioContext();
        const source = audioContext.createMediaStreamSource(mediaStream);
        await audioContext.audioWorklet.addModule("audioprocessor.js");
        audioWorklet = new AudioWorkletNode(audioContext, "audio-processor");
        audioWorklet.port.onmessage = (event) => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(event.data.buffer);
            }
        };
        source.connect(audioWorklet);
        audioWorklet.connect(audioContext.destination);
    } catch (err) {
        setStatus(`Lỗi microphone: ${err.message}`, "error");
    }
}

function stopAudioCapture() {
    audioWorklet?.disconnect(); audioWorklet = null;
    audioContext?.close(); audioContext = null;
    mediaStream?.getTracks().forEach(t => t.stop()); mediaStream = null;
}

// ─── Transcript Display ─────────────────────────────────────────

function updateTranscript(segments) {
    // UI v5: #transcript is a <textarea>, so we use .value not innerHTML
    const container = document.getElementById("transcript");

    for (const seg of segments) {
        if (seg.completed && !allCompletedSegments.some(s => s.start === seg.start && s.text === seg.text)) {
            allCompletedSegments.push({ ...seg });
        }
    }

    const inProgress = segments.filter(s => !s.completed);

    // Render as plain text into textarea (no HTML tags)
    const completedLines = allCompletedSegments.map(
        s => `[${formatTime(parseFloat(s.start))}] ${s.text}`
    );
    const inProgressLine = inProgress.length > 0
        ? `[${formatTime(parseFloat(inProgress[inProgress.length - 1].start))}] ${inProgress[inProgress.length - 1].text} ...`
        : '';
    const allLines = [...completedLines];
    if (inProgressLine) allLines.push(inProgressLine);

    container.value = allLines.join('\n');
    container.scrollTop = container.scrollHeight;

    const countEl = document.getElementById("segment-count");
    if (countEl) countEl.textContent = `${allCompletedSegments.length} đoạn`;

    // Enable buttons when transcript has content
    if (allCompletedSegments.length > 0) {
        const genBtn = document.getElementById("btn-generate-notes");
        const saveBtn = document.getElementById("btn-save-transcript");
        if (genBtn) genBtn.disabled = false;
        if (saveBtn) saveBtn.disabled = false;
    }
}

function getFullTranscript() {
    // Manual input takes priority if visible and has content
    const manual = document.getElementById("manual-transcript");
    if (manual && manual.style.display !== "none" && manual.value.trim()) {
        return manual.value.trim();
    }
    // Direct paste/type into main transcript textarea
    const live = document.getElementById("transcript");
    if (live && live.value.trim()) {
        return live.value.trim();
    }
    // Fall back to live segments from recording
    return allCompletedSegments.map(s => s.text.trim()).filter(Boolean).join(" ");
}

function toggleManualInput() {
    const manual = document.getElementById("manual-transcript");
    const auto = document.getElementById("transcript");
    const isHidden = manual.style.display === "none";
    manual.style.display = isHidden ? "block" : "none";
    auto.style.display = isHidden ? "none" : "block";
    if (isHidden) {
        document.getElementById("btn-generate-notes").disabled = false;
        document.getElementById("btn-save-transcript").disabled = false;
        manual.focus();
    }
}

// ─── File Upload ────────────────────────────────────────────────

async function uploadAudioFile(input) {
    const file = input.files[0];
    if (!file) return;

    const language = document.getElementById("language").value;
    const vocabHints = document.getElementById("vocab-hints").value.trim();
    setPaneStatus("transcript", `Đang upload "${file.name}"...`, "connecting");

    const formData = new FormData();
    formData.append("file", file);
    formData.append("language", language);
    if (vocabHints) formData.append("vocab_hints", vocabHints);

    try {
        const resp = await fetch(`${API_BASE}/api/transcribe`, { method: "POST", body: formData });
        if (!resp.ok) throw new Error((await resp.json()).detail || "Transcription failed");

        const result = await resp.json();
        const text = result.text || "";
        if (!text.trim()) { setPaneStatus("transcript", "Không phát hiện giọng nói trong file.", "error"); return; }

        allCompletedSegments = [{ start: "0.000", text, completed: true }];
        const container = document.getElementById("transcript");
        // textarea — use .value, not innerHTML
        container.value = `[upload] ${text}`;

        // Persist transcript into DB so Clean/Generate MoM read fresh segments —
        // otherwise Clean returns 400 "No segments" and MoM reads stale segments
        // from a sibling recording in the same meeting.
        console.log("[upload] before persist — meetingDbId=", window.meetingDbId,
                    "currentRecordingId=", window.currentRecordingId,
                    "pendingRecordingForMeetingId=", window.pendingRecordingForMeetingId);
        try {
            setPaneStatus("transcript", "Đang lưu transcript vào DB...", "assessing");
            const imported = await window.importTranscriptToDb(text);
            console.log("[upload] persist OK →", imported);
            window.currentRecordingId = imported.recordingId;
            setPaneStatus(
                "transcript",
                `Đã transcribe + lưu DB "${file.name}" ✓ (${imported.segmentCount} segments → rec ${imported.recordingId.slice(0,8)})`,
                "success",
            );
        } catch (persistErr) {
            console.error("[upload] persist failed:", persistErr);
            setPaneStatus(
                "transcript",
                `Đã transcribe nhưng lưu DB lỗi: ${persistErr.message}`,
                "error",
            );
        }
        setTimeout(() => setPaneStatus("transcript", "", ""), 3000);
        document.getElementById("btn-generate-notes").disabled = false;
        document.getElementById("btn-save-transcript").disabled = false;
    } catch (err) {
        setPaneStatus("transcript", `Lỗi upload: ${err.message}`, "error");
    }
    input.value = "";
}

// ─── Generate MoM ───────────────────────────────────────────────

async function generateNotes() {
    const transcript = getFullTranscript();
    if (!transcript) {
        alert("Chưa có transcript. Vui lòng ghi âm hoặc upload file audio trước.");
        return;
    }

    setPaneStatus("mom", "Đang chuẩn bị...", "assessing");
    document.getElementById("btn-generate-notes").disabled = true;
    document.getElementById("btn-new-session").style.display = "inline-block";

    try {
        // Per-recording MoM. Always re-import transcript so DB segments match
        // what's in the textarea (live record path doesn't auto-sync to DB).
        setPaneStatus("mom", "Đang lưu transcript vào DB...", "assessing");
        const imported = await window.importTranscriptToDb(transcript);
        const recordingId = imported.recordingId;
        if (!recordingId) {
            throw new Error("Không xác định được recording_id. Vui lòng chọn 1 phiên họp trước.");
        }
        setPaneStatus("mom", `Đang tạo biên bản phiên họp qua LangGraph (${imported.segmentCount} segments)...`, "assessing");

        const resp = await fetch(`${API_BASE}/api/recordings/${recordingId}/generate-mom`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || "Tạo biên bản thất bại");
        }

        const result = await resp.json();
        displayMoM(result.notes);
        window.currentRecordingMom = result.notes;
        window.currentMomLevel = "recording";

        // Per-recording download URL
        downloadUrl = `${API_BASE}/api/recordings/${recordingId}/download?fmt=md`;

        const downloadRow = document.getElementById("download-row");
        if (downloadRow) downloadRow.style.display = "flex";
        const dlBtn = document.getElementById("btn-download");
        if (dlBtn) dlBtn.disabled = false;
        const pdfBtn = document.getElementById("btn-pdf");
        if (pdfBtn) pdfBtn.disabled = false;

        const memCount = result.memory_context_count || 0;
        const memHint = memCount > 0 ? ` (dùng ${memCount} events từ memory)` : "";
        setPaneStatus("mom", `Đã tạo biên bản phiên ✓${memHint}`, "success");
        setTimeout(() => setPaneStatus("mom", "", ""), 4000);

        if (typeof window.reloadSidebarMeetings === 'function') {
            window.reloadSidebarMeetings();
        }
    } catch (err) {
        setPaneStatus("mom", `Lỗi: ${err.message}`, "error");
        document.getElementById("btn-generate-notes").disabled = false;
    }
}

// ─── Project Summary (tổng kết project) ─────────────────────────

async function generateProjectSummary() {
    if (!window.meetingDbId) {
        alert("Chưa chọn project nào.");
        return;
    }

    setPaneStatus("mom", "Đang tạo tổng kết project...", "assessing");
    const btn = document.getElementById("btn-project-summary");
    if (btn) btn.disabled = true;

    try {
        const resp = await fetch(
            `${API_BASE}/api/meetings/${window.meetingDbId}/generate-project-summary`,
            { method: "POST", headers: { "Content-Type": "application/json" } },
        );
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || "Tạo tổng kết thất bại");
        }
        const result = await resp.json();
        displayProjectSummary(result.summary);
        window.currentMomLevel = "project";
        setPaneStatus(
            "mom",
            `Đã tổng kết project ✓ (${result.summary.session_count} phiên)`,
            "success",
        );
        setTimeout(() => setPaneStatus("mom", "", ""), 4000);
    } catch (err) {
        setPaneStatus("mom", `Lỗi: ${err.message}`, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

function displayProjectSummary(summary) {
    const container = document.getElementById("mom-result");
    if (!container) return;

    let html = `<div class="mom-section">
        <div class="mom-section-title">📊 Tổng kết project: ${escapeHtml(summary.project_title || "")}</div>
        <div style="font-size:13px;color:#666;margin-bottom:12px;">
            ${summary.session_count} phiên họp · Tạo lúc: ${(summary.generated_at || "").slice(0, 19).replace("T", " ")}
        </div>`;

    if (summary.narrative) {
        html += `<div style="background:#f8f9fa;padding:12px;border-left:3px solid #4a90e2;border-radius:4px;margin-bottom:16px;font-style:italic;">
            ${escapeHtml(summary.narrative)}
        </div>`;
    }

    html += `<div class="mom-section-title" style="margin-top:16px;">⏱ Timeline decisions</div>`;
    const timeline = summary.decisions_timeline || [];
    if (timeline.length === 0) {
        html += `<div style="color:#888;padding:8px;">Chưa có decisions nào trong project này.</div>`;
    } else {
        html += `<div class="timeline">`;
        for (const entry of timeline) {
            const dt = (entry.date || "").slice(0, 10);
            html += `<div class="timeline-entry" style="border-left:2px solid #4a90e2;padding-left:14px;margin-bottom:14px;">
                <div style="font-weight:600;font-size:13px;color:#333;">${dt} — ${escapeHtml(entry.session_label || "")}</div>
                <ul style="margin:4px 0 0 18px;padding:0;">`;
            for (const dec of entry.decisions || []) {
                html += `<li style="margin:3px 0;font-size:13px;">${escapeHtml(dec)}</li>`;
            }
            html += `</ul></div>`;
        }
        html += `</div>`;
    }
    html += `</div>`;
    container.innerHTML = html;
}

window.generateProjectSummary = generateProjectSummary;

// ─── Button state sync ──────────────────────────────────────────
// Called whenever currentRecordingId / meetingDbId / transcript state changes.
// "Biên bản phiên này" enabled iff a recording is selected AND there's transcript.
// "Tổng kết project" enabled iff a meeting (project) is selected.
function updateMomButtonStates() {
    const hasRecording = !!window.currentRecordingId;
    const hasProject   = !!window.meetingDbId;
    const hasTranscript = (getFullTranscript() || "").trim().length > 0;

    const genBtn = document.getElementById("btn-generate-notes");
    if (genBtn) {
        genBtn.disabled = !(hasRecording && hasTranscript);
        genBtn.title = !hasRecording
            ? "Chọn 1 phiên họp trước khi tạo biên bản"
            : (!hasTranscript ? "Chưa có transcript" : "Tạo biên bản cho phiên đang xem");
    }
    const sumBtn = document.getElementById("btn-project-summary");
    if (sumBtn) {
        sumBtn.disabled = !hasProject;
        sumBtn.title = hasProject
            ? "Tổng kết tất cả phiên họp trong project (timeline decisions)"
            : "Chọn 1 project trước";
    }
}
window.updateMomButtonStates = updateMomButtonStates;
// Wire to transcript edits so button enables/disables live
document.addEventListener("DOMContentLoaded", () => {
    const t = document.getElementById("transcript");
    if (t) t.addEventListener("input", updateMomButtonStates);
    updateMomButtonStates();
});

function displayMoM(notes) {
    const container = document.getElementById("mom-result");
    let html = "";

    // Header info
    html += `<div class="mom-section">
        <div class="mom-section-title">Thông tin cuộc họp</div>
        <table class="mom-meta-table">
            <tr><td>Mục đích / Purpose</td><td>${escapeHtml(notes.purpose || "")}</td></tr>
            <tr><td>Địa điểm / Venue</td><td>${escapeHtml(notes.venue || "")}</td></tr>
            <tr><td>Ngày họp / Date</td><td>${escapeHtml(notes.date || "")}</td></tr>
            <tr><td>Người chủ trì / Chaired by</td><td>${escapeHtml(notes.chaired_by || "")}</td></tr>
            <tr><td>Thư ký / Noted by</td><td>${escapeHtml(notes.noted_by || "")}</td></tr>
        </table>
    </div>`;

    // Attendees
    const attendees = notes.attendees || [];
    if (attendees.length > 0) {
        html += `<div class="mom-section">
            <div class="mom-section-title">Thành phần tham gia</div>
            <table class="mom-table">
                <tr><th>No.</th><th>Họ và tên</th><th>Đơn vị</th><th>Chức vụ</th></tr>
                ${attendees.map((a, i) => `
                    <tr>
                        <td>${i + 1}</td>
                        <td>${escapeHtml(a.name || "")}</td>
                        <td>${escapeHtml(a.department || "")}</td>
                        <td>${escapeHtml(a.title || "")}</td>
                    </tr>`).join("")}
            </table>
        </div>`;
    }

    // Agenda items
    const agendaItems = notes.agenda_items || [];
    if (agendaItems.length > 0) {
        html += `<div class="mom-section">
            <div class="mom-section-title">Nội dung cuộc họp</div>`;
        for (const item of agendaItems) {
            html += `<div class="agenda-item">
                <div class="agenda-item-header">
                    <span class="topic-no">${item.topic_no}</span>
                    <span class="agenda-title">${escapeHtml(item.agenda || "")}</span>
                </div>
                <div class="agenda-description">${escapeHtml(item.description || "")}</div>
            </div>`;
        }
        html += `</div>`;
    }

    // Action items
    const actionItems = notes.action_items || [];
    if (actionItems.length > 0) {
        // Group consecutive items with the same PIC — only render the PIC name
        // on the first row of each group; the rest get an empty PIC cell with
        // a `.merged` class for visual continuity (faint left border).
        let prevPic = null;
        const itemsHtml = actionItems.map(a => {
            const pic = (a.pic || "").trim();
            const sameAsPrev = pic && pic === prevPic;
            prevPic = pic;
            return `
                <div class="action-item${sameAsPrev ? " merged" : ""}">
                    <span class="action-pic">${sameAsPrev ? "" : escapeHtml(pic)}</span>
                    <span class="action-task">${escapeHtml(a.item || "")}</span>
                    <span class="action-deadline">${escapeHtml(a.deadline || "")}</span>
                </div>`;
        }).join("");
        html += `<div class="mom-section">
            <div class="mom-section-title">Các công việc tiếp theo</div>
            <div style="border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden;">
                ${itemsHtml}
            </div>
        </div>`;
    }

    // Summary
    if (notes.summary) {
        html += `<div class="mom-section">
            <div class="mom-section-title">Tóm tắt</div>
            <div class="mom-summary">${escapeHtml(notes.summary)}</div>
        </div>`;
    }

    container.innerHTML = html;
    container.scrollTop = 0;

    // Hide empty state
    const momEmpty = document.getElementById("mom-empty");
    if (momEmpty) momEmpty.style.display = "none";

    // Enable download/PDF buttons now that MoM is on screen.
    const dl = document.getElementById("btn-download");
    const pdf = document.getElementById("btn-pdf");
    if (dl)  dl.disabled = false;
    if (pdf) pdf.disabled = false;
}
// Expose so sidebar load-meeting can render
window.displayMoM = displayMoM;

function downloadMoM() {
    // Tải .md từ backend (re-generate trên server từ meetings.mom_json).
    if (!window.meetingDbId) {
        alert("Chưa có meeting để tải.");
        return;
    }
    window.location.href = `${API_BASE}/api/meetings/${window.meetingDbId}/download?fmt=md`;
}

function downloadPDF() {
    // Mở print dialog — user "Save as PDF" hoặc print trực tiếp.
    // CSS @media print sẽ ẩn sidebar/chat, chỉ giữ MoM panel.
    if (!window.meetingDbId) {
        alert("Chưa có meeting để in.");
        return;
    }
    window.print();
}

// ─── Save Transcript ─────────────────────────────────────────────

function saveTranscript() {
    const transcript = getFullTranscript();
    if (!transcript) { alert("Chưa có transcript."); return; }

    const title = document.getElementById("meeting-title").value.trim() || "meeting";
    const dateStr = document.getElementById("meeting-date").value || new Date().toISOString().split("T")[0];

    let content = `Title: ${title}\nDate: ${dateStr}\nSegments: ${allCompletedSegments.length}\n${"=".repeat(60)}\n\n`;
    for (const seg of allCompletedSegments) {
        content += `[${formatTime(parseFloat(seg.start))}] ${seg.text.trim()}\n`;
    }

    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `transcript_${title.replace(/\s+/g, "_")}_${dateStr}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    setStatus("Đã lưu transcript.", "idle");
}

// ─── Timer ──────────────────────────────────────────────────────

function startTimer() {
    startTime = Date.now();
    const timerEl = document.getElementById("timer");
    timerEl.style.display = "block";
    timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        timerEl.textContent = [
            Math.floor(elapsed / 3600),
            Math.floor((elapsed % 3600) / 60),
            elapsed % 60,
        ].map(n => String(n).padStart(2, "0")).join(":");
    }, 1000);
}

function stopTimer() {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
}

// ─── New Session ────────────────────────────────────────────────

function newSession() {
    // Reset state for a new meeting without reloading the page
    allCompletedSegments = [];
    sessionId = null;
    downloadUrl = null;
    wsReconnectAttempts = 0;

    // Phase D: reset DB meeting + chat session state
    window.meetingDbId = null;
    // Clear any leftover "Phiên họp mới cho..." flag + banner
    window.pendingRecordingForMeetingId = null;
    document.getElementById('new-recording-banner')?.remove();
    if (typeof window._restoreTitlePlaceholder === 'function') window._restoreTitlePlaceholder();
    // Exit project overview mode (newSession = fresh start, not overview)
    document.body.classList.remove('project-overview-mode');
    if (typeof chatSessionId !== 'undefined') {
        try { chatSessionId = null; } catch (e) {}
    }
    // Also clear via window if defined in IIFE
    if ('chatSessionId' in window) window.chatSessionId = null;

    // Clear transcript textarea (UI v5: textarea, not div)
    const transcript = document.getElementById("transcript");
    if (transcript) transcript.value = "";
    const manual = document.getElementById("manual-transcript");
    if (manual) { manual.value = ""; }

    // Clear MoM result
    const momResult = document.getElementById("mom-result");
    if (momResult) momResult.innerHTML = "";
    const momEmpty = document.getElementById("mom-empty");
    if (momEmpty) momEmpty.style.display = "";

    // Clear chat thread (keep welcome + suggested prompts)
    const chatThread = document.getElementById("chat-thread");
    if (chatThread) {
        // Remove everything except first welcome msg + suggested prompts
        const keep = chatThread.querySelectorAll('.msg-agent:first-child, #suggested-prompts');
        chatThread.innerHTML = '';
        keep.forEach(el => chatThread.appendChild(el));
        // Re-show suggested prompts
        const prompts = document.getElementById('suggested-prompts');
        if (prompts) prompts.style.display = '';
    }

    // Reset buttons
    const btnStart = document.getElementById("btn-start");
    if (btnStart) btnStart.disabled = false;
    const btnStop = document.getElementById("btn-stop");
    if (btnStop) btnStop.disabled = true;
    const btnGen = document.getElementById("btn-generate-notes");
    if (btnGen) btnGen.disabled = true;
    const btnSave = document.getElementById("btn-save-transcript");
    if (btnSave) btnSave.disabled = true;
    const dlRow = document.getElementById("download-row");
    if (dlRow) dlRow.style.display = "none";
    const dlBtn = document.getElementById("btn-download");
    if (dlBtn) dlBtn.disabled = true;
    const pdfBtn = document.getElementById("btn-pdf");
    if (pdfBtn) pdfBtn.disabled = true;

    // Reset meeting fields
    const titleEl = document.getElementById("meeting-title");
    if (titleEl) titleEl.value = "";
    const purposeEl = document.getElementById("meeting-purpose");
    if (purposeEl) purposeEl.value = "";
    const venueEl = document.getElementById("meeting-venue");
    if (venueEl) venueEl.value = "";
    const chairEl = document.getElementById("chaired-by");
    if (chairEl) chairEl.value = "";
    const notedEl = document.getElementById("noted-by");
    if (notedEl) notedEl.value = "";
    const attEl = document.getElementById("attendees");
    if (attEl) attEl.value = "";
    const dateEl = document.getElementById("meeting-date");
    if (dateEl) dateEl.value = new Date().toISOString().split("T")[0];

    const countEl = document.getElementById("segment-count");
    if (countEl) countEl.textContent = "0";
    stopTimer();
    const timerEl = document.getElementById("timer");
    if (timerEl) timerEl.textContent = "00:00";
    setStatus("Sẵn sàng", "idle");

    // Update meta info display
    if (typeof window.updateMeta === 'function') window.updateMeta();
}
// Expose for IIFE buttons
window.newSession = newSession;

// ─── Utilities ──────────────────────────────────────────────────

function setStatus(text, state) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.className = `status ${state}`;
}

/**
 * Contextual status — shown as an inline banner INSIDE the relevant pane's
 * content area (above the textarea / MoM area), not in the header bar.
 *   target='transcript' → above "Biên bản gốc" textarea (upload / clean)
 *   target='mom'        → above "Biên bản họp" content (generate MoM)
 * `text` empty → hide the banner.
 */
function setPaneStatus(target, text, state) {
    const id = target === 'mom' ? 'mom-inline-status' : 'transcript-inline-status';
    const el = document.getElementById(id);
    if (!el) return;
    if (!text) {
        el.classList.add('hidden');
        el.textContent = '';
        el.className = `pane-inline-status hidden`;
        return;
    }
    el.classList.remove('hidden');
    el.textContent = text;
    el.className = `pane-inline-status ${state || ''}`.trim();
}
window.setPaneStatus = setPaneStatus;

function formatTime(seconds) {
    return [Math.floor(seconds / 60), Math.floor(seconds % 60)].map(n => String(n).padStart(2, "0")).join(":");
}

function formatDateVN(isoDate) {
    if (!isoDate) return "";
    const [y, m, d] = isoDate.split("-");
    return `${d}/${m}/${y}`;
}

function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function fetchWhisperPrompt(vocabHints, language) {
    try {
        const params = new URLSearchParams({ language, vocab_hints: vocabHints });
        const resp = await fetch(`${API_BASE}/api/vocab-pool/whisper-prompt?${params}`);
        if (!resp.ok) throw new Error();
        const data = await resp.json();
        return data.prompt;
    } catch {
        // fallback to local build if server unreachable
        return buildWhisperPrompt(vocabHints, language);
    }
}

function buildWhisperPrompt(vocabHints, language) {
    if (language !== "vi") return vocabHints || undefined;
    let prompt =
        "Đây là bản ghi cuộc họp nội bộ bằng tiếng Việt. " +
        "Người nói có thể dùng xen kẽ các từ tiếng Anh kỹ thuật như: " +
        "API, backend, frontend, deploy, pipeline, sprint, backlog, " +
        "roadmap, OKR, KPI, deadline, meeting, update, review, " +
        "feature, bug, fix, release, merge, commit, dashboard, report. " +
        "Giữ nguyên các từ tiếng Anh, không dịch sang tiếng Việt.";
    if (vocabHints) prompt += ` Chủ đề cuộc họp liên quan đến: ${vocabHints}.`;
    return prompt;
}

// ─── Vocab Pool ─────────────────────────────────────────────────

async function loadVocabPool() {
    try {
        const resp = await fetch(`${API_BASE}/api/vocab-pool`);
        if (!resp.ok) return;
        const pool = await resp.json();
        renderVocabPool(pool);
    } catch { /* server not running yet, skip */ }
}

function renderVocabPool(pool) {
    const countEl = document.getElementById("vocab-count");
    const listEl = document.getElementById("corrections-list");
    if (!countEl || !listEl) return;

    const termCount = (pool.terms || []).length;
    countEl.textContent = termCount > 0 ? `(${termCount} terms)` : "";

    const corrections = pool.corrections || {};
    listEl.innerHTML = "";
    if (Object.keys(corrections).length === 0) {
        listEl.innerHTML = '<span style="font-size:0.72rem;color:var(--text-light);padding:2px 0">Chưa có sửa lỗi nào.</span>';
        return;
    }
    for (const [wrong, correct] of Object.entries(corrections)) {
        const tag = document.createElement("div");
        tag.className = "correction-tag";
        tag.innerHTML = `
            <span class="wrong">${escapeHtml(wrong)}</span>
            <span class="arrow">→</span>
            <span class="correct">${escapeHtml(correct)}</span>
            <button class="del-btn" onclick="deleteCorrection('${escapeHtml(wrong)}')" title="Xoá">✕</button>`;
        listEl.appendChild(tag);
    }
}

async function addCorrection() {
    const wrong = document.getElementById("correction-wrong").value.trim();
    const correct = document.getElementById("correction-correct").value.trim();
    if (!wrong || !correct) { alert("Vui lòng nhập cả hai ô."); return; }

    const resp = await fetch(`${API_BASE}/api/vocab-pool/corrections`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wrong, correct }),
    });
    if (!resp.ok) return;
    const data = await resp.json();
    renderVocabPool(data.pool);
    document.getElementById("correction-wrong").value = "";
    document.getElementById("correction-correct").value = "";
}

async function deleteCorrection(wrong) {
    await fetch(`${API_BASE}/api/vocab-pool/corrections/${encodeURIComponent(wrong)}`, { method: "DELETE" });
    loadVocabPool();
}
