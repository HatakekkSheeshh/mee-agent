"""
FastAPI application for the Meeting Note Agent.
Serves the web UI, handles note generation requests, and provides Markdown downloads.
"""
import io
import logging
import os
import threading
import uuid
from datetime import datetime

import requests
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from meeting.api.meetings import router as meetings_router
from meeting.memory_client import save_meeting_events
from meeting.note_generator import generate_meeting_notes
from meeting.report_generator import generate_mom_markdown
from meeting.vocab_store import (
    load_pool, add_correction, delete_correction,
    extract_and_save_terms, build_pool_prompt_fragment,
)

logging.basicConfig(level=logging.INFO)

# In-memory session store
sessions: dict = {}


class MeetingInfo(BaseModel):
    title: str = ""
    purpose: str = ""
    venue: str = ""
    date: str = ""
    chaired_by: str = ""
    noted_by: str = ""
    attendees: str = ""  # comma-separated names, or freeform


class NoteRequest(BaseModel):
    session_id: str
    title: str = ""
    purpose: str = ""
    venue: str = ""
    date: str = ""
    chaired_by: str = ""
    noted_by: str = ""
    attendees: str = ""
    transcript: str


class CorrectionRequest(BaseModel):
    wrong: str
    correct: str


def _build_whisper_prompt(vocab_hints: str = "", language: str = "vi") -> str:
    """
    Build an initial_prompt for Whisper to improve Vietnamese transcription quality.

    Why this works:
    - Whisper treats the prompt as "preceding transcript context", so it mimics
      the style, vocabulary, and language-mixing pattern shown in the prompt.
    - A Vietnamese base sentence anchors the model to vi locale and reduces
      hallucinations on silent/noisy segments.
    - Listing English tech terms tells Whisper to keep them verbatim instead of
      translating (e.g. "deploy" → "triển khai").
    """
    if language != "vi":
        return vocab_hints.strip() if vocab_hints.strip() else ""

    base = (
        "Đây là bản ghi cuộc họp nội bộ bằng tiếng Việt. "
        "Người nói có thể dùng xen kẽ các từ tiếng Anh kỹ thuật như: "
        "API, backend, frontend, deploy, pipeline, sprint, backlog, "
        "roadmap, OKR, KPI, deadline, meeting, update, review, "
        "feature, bug, fix, release, merge, commit, dashboard, report. "
        "Giữ nguyên các từ tiếng Anh, không dịch sang tiếng Việt."
    )
    if vocab_hints.strip():
        base += f" Chủ đề cuộc họp liên quan đến: {vocab_hints.strip()}."
    pool_fragment = build_pool_prompt_fragment()
    if pool_fragment:
        base += " " + pool_fragment
    return base


def create_app(output_dir: str = None) -> FastAPI:
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")

    app = FastAPI(title="Meeting Note Agent")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "meeting_frontend")

    # Phase A — DB-backed meetings router
    app.include_router(meetings_router)

    @app.post("/api/session")
    async def create_session(info: MeetingInfo):
        session_id = str(uuid.uuid4())[:8]
        sessions[session_id] = {
            "title": info.title,
            "purpose": info.purpose,
            "venue": info.venue,
            "date": info.date or datetime.now().strftime("%d/%m/%Y"),
            "chaired_by": info.chaired_by,
            "noted_by": info.noted_by,
            "attendees": info.attendees,
            "created_at": datetime.now().isoformat(),
            "transcript": "",
            "notes": None,
            "md_path": None,
        }
        return {"session_id": session_id}

    @app.post("/api/generate-notes")
    async def run_note_generation(req: NoteRequest):
        """Generate MoM from transcript using Claude CLI."""
        if not req.transcript.strip():
            raise HTTPException(status_code=400, detail="Transcript is empty")

        logging.info(f"Generating notes for session {req.session_id}, transcript length: {len(req.transcript)}")

        # Save transcript backup
        meeting_date = req.date or datetime.now().strftime("%Y-%m-%d")
        safe_title = (req.title or "meeting").replace(" ", "_").replace("/", "-")[:30]
        safe_date = meeting_date.replace("/", "-").replace(" ", "_")
        transcript_path = os.path.join(
            output_dir, f"transcript_{safe_title}_{safe_date}_{req.session_id}.txt"
        )
        os.makedirs(output_dir, exist_ok=True)
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(f"Title: {req.title}\n")
            f.write(f"Date: {req.date}\n")
            f.write(f"Chaired by: {req.chaired_by}\n")
            f.write(f"Attendees: {req.attendees}\n")
            f.write("=" * 60 + "\n\n")
            f.write(req.transcript)
        logging.info(f"Transcript saved: {transcript_path}")

        # Generate notes via Claude
        notes = generate_meeting_notes(
            transcript=req.transcript,
            title=req.title,
            purpose=req.purpose,
            date=req.date,
            chaired_by=req.chaired_by,
            noted_by=req.noted_by,
            venue=req.venue,
            attendees=req.attendees,
        )

        if "error" in notes:
            raise HTTPException(
                status_code=500,
                detail=f"{notes['error']}. Transcript saved at: {transcript_path}",
            )

        # Generate Markdown report
        md_path = generate_mom_markdown(notes=notes, output_dir=output_dir)

        # Store in session
        sessions[req.session_id] = {
            **sessions.get(req.session_id, {}),
            "transcript": req.transcript,
            "notes": notes,
            "md_path": md_path,
        }

        # Extract vocab terms in background — non-blocking
        threading.Thread(
            target=extract_and_save_terms, args=(notes,), daemon=True
        ).start()

        # Save meeting events to AgentBase Memory — non-blocking
        threading.Thread(
            target=save_meeting_events,
            args=(req.session_id, notes, req.transcript),
            daemon=True,
        ).start()

        return {
            "session_id": req.session_id,
            "notes": notes,
            "download_url": f"/api/download/{req.session_id}",
        }

    @app.get("/api/download/{session_id}")
    async def download_markdown(session_id: str):
        session = sessions.get(session_id)
        if not session or not session.get("md_path"):
            raise HTTPException(status_code=404, detail="Notes not found")

        md_path = session["md_path"]
        if not os.path.exists(md_path):
            raise HTTPException(status_code=404, detail="Markdown file not found")

        return FileResponse(
            md_path,
            media_type="text/markdown",
            filename=os.path.basename(md_path),
        )

    @app.post("/api/transcribe")
    async def transcribe_file(
        file: UploadFile = File(...),
        language: str = Form(default="vi"),
        vocab_hints: str = Form(default=""),
    ):
        """Upload an audio file and transcribe via VNGCloud MaaS Whisper API."""
        base_url = os.getenv("WHISPER_BASE_URL", "").rstrip("/")
        api_key = os.getenv("WHISPER_API_KEY", "")
        model = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")

        if not base_url:
            raise HTTPException(status_code=500, detail="WHISPER_BASE_URL not configured")

        audio_bytes = await file.read()
        filename = file.filename or "audio.wav"

        if not filename.lower().endswith(".wav"):
            try:
                audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
                wav_buf = io.BytesIO()
                sf.write(wav_buf, audio_data, sample_rate, format="WAV", subtype="PCM_16")
                wav_buf.seek(0)
                audio_bytes = wav_buf.read()
                filename = "audio.wav"
            except Exception:
                pass

        prompt = _build_whisper_prompt(vocab_hints, language)

        try:
            resp = requests.post(
                f"{base_url}/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (filename, audio_bytes, "audio/wav")},
                data={
                    "model": model,
                    "language": language,
                    "response_format": "json",
                    "prompt": prompt,
                },
                timeout=60,
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "")
            hallucination_words = [
                "subscribe", "la la school", "ghiền mì gõ", "không bỏ lỡ",
                "video hấp dẫn", "cảm ơn các bạn đã theo dõi", "nhớ like",
                "nhấn chuông", "đăng ký kênh", "like share", "kênh youtube",
            ]
            text_lower = text.lower()
            for hw in hallucination_words:
                if hw in text_lower:
                    text = ""
                    break
            return {"text": text}
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Whisper API error: {str(e)}")

    @app.get("/api/vocab-pool")
    async def get_vocab_pool():
        return load_pool()

    @app.post("/api/vocab-pool/corrections")
    async def add_vocab_correction(req: CorrectionRequest):
        if not req.wrong.strip() or not req.correct.strip():
            raise HTTPException(status_code=400, detail="Both fields required")
        add_correction(req.wrong, req.correct)
        return {"ok": True, "pool": load_pool()}

    @app.delete("/api/vocab-pool/corrections/{wrong}")
    async def delete_vocab_correction(wrong: str):
        delete_correction(wrong)
        return {"ok": True}

    @app.get("/api/vocab-pool/whisper-prompt")
    async def get_whisper_prompt(language: str = "vi", vocab_hints: str = ""):
        """Frontend calls this to get the full prompt to use for WebSocket config."""
        return {"prompt": _build_whisper_prompt(vocab_hints, language)}

    @app.get("/api/sessions")
    async def list_sessions():
        return {
            sid: {
                "title": s.get("title"),
                "date": s.get("date"),
                "has_notes": s.get("notes") is not None,
            }
            for sid, s in sessions.items()
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Serve frontend static files
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
