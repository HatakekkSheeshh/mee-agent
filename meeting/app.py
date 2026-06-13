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

from contextlib import asynccontextmanager

from meeting.api.chat import router as chat_router
from meeting.api.meetings import router as meetings_router
from meeting.graphs import close_checkpointer, init_checkpointer
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


WHISPER_FILE_SIZE_LIMIT = 24 * 1024 * 1024  # 24MB safe under Whisper 25MB hard limit
CHUNK_DURATION_SEC = 10 * 60  # 10-min chunks → ~19MB at 16kHz mono PCM16

# Hallucination words list (Whisper sometimes outputs these from silence/noise)
WHISPER_HALLUCINATIONS = [
    "subscribe", "la la school", "ghiền mì gõ", "không bỏ lỡ",
    "video hấp dẫn", "cảm ơn các bạn đã theo dõi", "nhớ like",
    "nhấn chuông", "đăng ký kênh", "like share", "kênh youtube",
]


def _filter_hallucinations(text: str) -> str:
    """Return empty string if text matches known Whisper hallucination patterns."""
    text_lower = text.lower()
    for hw in WHISPER_HALLUCINATIONS:
        if hw in text_lower:
            return ""
    return text


def _call_whisper_api(
    audio_bytes: bytes,
    filename: str,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    language: str,
    timeout: int = 120,
) -> dict:
    """Single Whisper API call. Returns full dict:
        {text, segments?, cluster_embeddings?, language?}
    PhoWhisper-server includes segments + cluster_embeddings; VNG MaaS Whisper
    returns only text. Caller handles both.
    """
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
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json()
    payload["text"] = _filter_hallucinations(payload.get("text", ""))
    return payload


def _chunk_and_transcribe(
    audio_bytes: bytes,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    language: str,
) -> tuple[str, int]:
    """
    Split large audio into 10-min chunks (mono 16kHz WAV), transcribe each,
    concat results. Returns (joined_text, chunk_count).

    Uses soundfile to read source (handles MP3/WAV/FLAC), then writes WAV chunks
    in-memory before sending to Whisper. Resampling to 16kHz handled via numpy.
    """
    import numpy as np  # local import — only needed in chunking path

    logging.info(f"[chunking] source size = {len(audio_bytes) / 1024 / 1024:.1f}MB")

    # Read full audio → numpy (any format soundfile supports)
    audio_data, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32")

    # Stereo → mono
    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)

    # Resample to 16kHz if needed (Whisper expects 16k anyway)
    if sr != 16000:
        ratio = 16000 / sr
        new_length = int(len(audio_data) * ratio)
        audio_data = np.interp(
            np.linspace(0, len(audio_data), new_length, endpoint=False),
            np.arange(len(audio_data)),
            audio_data,
        ).astype("float32")
        sr = 16000

    duration_sec = len(audio_data) / sr
    logging.info(f"[chunking] duration = {duration_sec:.1f}s, sample_rate = {sr}")

    chunk_size = CHUNK_DURATION_SEC * sr  # samples per chunk
    n_chunks = (len(audio_data) + chunk_size - 1) // chunk_size
    logging.info(f"[chunking] splitting into {n_chunks} chunks of {CHUNK_DURATION_SEC}s each")

    transcripts = []
    for idx in range(n_chunks):
        start = idx * chunk_size
        end = min(start + chunk_size, len(audio_data))
        chunk = audio_data[start:end]

        # Write chunk as in-memory WAV
        wav_buf = io.BytesIO()
        sf.write(wav_buf, chunk, sr, format="WAV", subtype="PCM_16")
        wav_buf.seek(0)
        chunk_bytes = wav_buf.read()

        chunk_mb = len(chunk_bytes) / 1024 / 1024
        logging.info(
            f"[chunking] chunk {idx+1}/{n_chunks} ({chunk_mb:.1f}MB) → Whisper"
        )

        try:
            resp = _call_whisper_api(
                chunk_bytes, f"chunk_{idx+1}.wav",
                base_url, api_key, model, prompt, language,
            )
            transcripts.append(resp.get("text", ""))
        except Exception as e:
            logging.error(f"[chunking] chunk {idx+1} failed: {e}")
            transcripts.append(f"[chunk {idx+1} failed: {e}]")

    return "\n".join(t for t in transcripts if t), n_chunks


def _build_whisper_prompt(
    vocab_hints: str = "",
    language: str = "vi",
    attendees: str = "",
) -> str:
    """
    Build an initial_prompt for Whisper to improve Vietnamese transcription quality.

    Why this works:
    - Whisper treats the prompt as "preceding transcript context", so it mimics
      the style, vocabulary, and language-mixing pattern shown in the prompt.
    - A Vietnamese base sentence anchors the model to vi locale and reduces
      hallucinations on silent/noisy segments.
    - Listing English tech terms tells Whisper to keep them verbatim instead of
      translating (e.g. "deploy" → "triển khai").
    - Including attendee names helps Whisper preserve them correctly (Sprint B).
    """
    if language != "vi":
        parts = []
        if attendees.strip():
            parts.append(f"Meeting attendees: {attendees.strip()}.")
        if vocab_hints.strip():
            parts.append(vocab_hints.strip())
        return " ".join(parts)

    base = (
        "Đây là bản ghi cuộc họp nội bộ bằng tiếng Việt. "
        "Người nói có thể dùng xen kẽ các từ tiếng Anh kỹ thuật như: "
        "API, backend, frontend, deploy, pipeline, sprint, backlog, "
        "roadmap, OKR, KPI, deadline, meeting, update, review, "
        "feature, bug, fix, release, merge, commit, dashboard, report. "
        "Giữ nguyên các từ tiếng Anh, không dịch sang tiếng Việt."
    )
    if attendees.strip():
        # Names hint helps Whisper preserve them (Sprint B)
        base += f" Tham dự cuộc họp: {attendees.strip()}."
    if vocab_hints.strip():
        base += f" Chủ đề cuộc họp liên quan đến: {vocab_hints.strip()}."
    pool_fragment = build_pool_prompt_fragment()
    if pool_fragment:
        base += " " + pool_fragment
    return base


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: init OTel tracing + LangGraph PostgresSaver + discover/register
    Redmine MCP tools. Shutdown: flush traces + close pool."""
    # Best-effort, env-gated: no-op unless OTEL_ENABLED / LANGFUSE_ENABLED is set.
    from meeting.observability import init_tracing, shutdown_tracing

    init_tracing(app)
    await init_checkpointer()
    # Best-effort: registers the deployed Redmine MCP server's tools into the
    # local registry (disk cache → live list_tools). Returns [] + logs if the
    # server is unreachable, so the app still boots without Redmine.
    from meeting.services import load_and_register_redmine_tools
    await load_and_register_redmine_tools()
    yield
    await close_checkpointer()
    shutdown_tracing()


def create_app(output_dir: str = None) -> FastAPI:
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")

    app = FastAPI(title="Meeting Note Agent", lifespan=_lifespan)
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
    # Phase B2 — Chat + HITL router
    app.include_router(chat_router)

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
        language: str = Form(default=""),
        vocab_hints: str = Form(default=""),
        attendees: str = Form(default=""),
        stt_model: str = Form(default=""),
    ):
        """Upload an audio file and transcribe via the chosen STT backend.

        STT backend resolution priority:
          1. `stt_model` form param (e.g. "whisper", "phowhisper")
          2. legacy env (WHISPER_*) for backward compat
        `language` defaults to the STT profile's language when not given
        (PhoWhisper → 'vi', Whisper → 'auto').

        Auto-chunking: MaaS Whisper has a 25MB upload limit — files > 24MB
        are split into 10-min chunks. Self-hosted PhoWhisper has no cap.
        """
        from meeting.services.model_registry import resolve_stt
        profile = resolve_stt(recording_choice=(stt_model or None))
        base_url = (profile.get("base_url") or "").rstrip("/")
        api_key = profile.get("api_key") or ""
        model = profile.get("model") or os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")
        if not language.strip():
            language = profile.get("language") or "vi"

        if not base_url:
            raise HTTPException(
                status_code=500,
                detail=f"STT profile '{profile.get('id')}' missing base_url — "
                       f"set {profile['env']['base_url']} in .env",
            )
        logging.info(
            f"[/api/transcribe] STT={profile.get('id')} model={model} language={language}"
        )

        audio_bytes = await file.read()
        filename = file.filename or "audio.wav"
        original_size_mb = len(audio_bytes) / 1024 / 1024

        prompt = _build_whisper_prompt(vocab_hints, language, attendees)

        # ─── Auto-chunking path for large files ───
        # Self-hosted PhoWhisper has no upload size cap and runs diarization on
        # the FULL audio in one shot — chunking would split a speaker across
        # chunks and lose cross-chunk cluster alignment + embeddings. Only
        # chunk when talking to size-capped backends (VNG MaaS Whisper: 25MB).
        is_self_hosted = (
            model.lower() == "phowhisper"
            or "59.153.246.55" in base_url
            or "localhost" in base_url
            or "127.0.0.1" in base_url
        )
        if len(audio_bytes) > WHISPER_FILE_SIZE_LIMIT and not is_self_hosted:
            logging.info(
                f"File {filename} is {original_size_mb:.1f}MB > 24MB threshold → auto-chunking"
            )
            try:
                text, n_chunks = _chunk_and_transcribe(
                    audio_bytes, base_url, api_key, model, prompt, language,
                )
                return {
                    "text": text,
                    "chunked": True,
                    "chunks": n_chunks,
                    "original_size_mb": round(original_size_mb, 1),
                }
            except Exception as e:
                logging.exception("Auto-chunk transcribe failed")
                raise HTTPException(
                    status_code=500,
                    detail=f"Chunked transcribe failed: {e}",
                )
        if len(audio_bytes) > WHISPER_FILE_SIZE_LIMIT and is_self_hosted:
            logging.info(
                f"File {filename} is {original_size_mb:.1f}MB > 24MB but backend is "
                f"self-hosted PhoWhisper — sending full audio to preserve diarization "
                f"+ cluster embeddings."
            )

        # ─── Standard path: single Whisper call ───
        # Convert non-WAV to WAV in-memory (Whisper preferred format).
        # MaaS Whisper rejects anything not RIFF-headed with HTTP 500
        # "file does not start with RIFF id". soundfile handles
        # WAV/FLAC/OGG/AIFF natively; m4a/aac/opus/webm need ffmpeg.
        if not filename.lower().endswith(".wav"):
            converted = False
            # Try soundfile first (zero-dep, in-process).
            try:
                audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
                wav_buf = io.BytesIO()
                sf.write(wav_buf, audio_data, sample_rate, format="WAV", subtype="PCM_16")
                audio_bytes = wav_buf.getvalue()
                filename = "audio.wav"
                converted = True
                logging.info(
                    f"[transcribe] sf-converted {file.filename} → WAV "
                    f"({sample_rate}Hz, {len(audio_bytes)//1024}KB)"
                )
            except Exception as sf_err:
                logging.warning(
                    f"[transcribe] soundfile cannot read {file.filename}: {sf_err}. "
                    f"Trying ffmpeg fallback for m4a/aac/opus/webm/etc…"
                )
            # ffmpeg fallback for codecs soundfile can't handle.
            if not converted:
                try:
                    import subprocess
                    proc = subprocess.run(
                        [
                            "ffmpeg", "-loglevel", "error", "-y",
                            "-i", "pipe:0",
                            "-ac", "1", "-ar", "16000",
                            "-f", "wav", "pipe:1",
                        ],
                        input=audio_bytes, capture_output=True, timeout=120,
                    )
                    if proc.returncode != 0 or not proc.stdout:
                        raise RuntimeError(
                            f"ffmpeg rc={proc.returncode}: {proc.stderr.decode('utf-8','replace')[:300]}"
                        )
                    audio_bytes = proc.stdout
                    filename = "audio.wav"
                    converted = True
                    logging.info(
                        f"[transcribe] ffmpeg-converted {file.filename} → WAV "
                        f"({len(audio_bytes)//1024}KB)"
                    )
                except FileNotFoundError:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            f"Cannot decode {file.filename}: soundfile failed and "
                            f"ffmpeg not installed. Install ffmpeg or upload WAV/FLAC/OGG."
                        ),
                    )
                except Exception as ff_err:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Cannot decode audio file {file.filename}. "
                            f"Tried soundfile + ffmpeg, both failed. "
                            f"ffmpeg error: {ff_err}"
                        ),
                    )

        # Scale timeout with file size — PhoWhisper on L40 ~1x realtime for STT
        # + diarization (~30s of audio per second of wall clock). 50MB PCM 16k
        # mono ≈ 27 min → up to ~3 min processing. Padding to 600s buys safety.
        # ≤24MB: keep the original 180s tight bound (faster fail on hangs).
        api_timeout = 600 if original_size_mb > 24 else 180
        try:
            resp = _call_whisper_api(
                audio_bytes, filename, base_url, api_key, model, prompt, language,
                timeout=api_timeout,
            )
            text = resp.get("text", "")
            segments = resp.get("segments")
            cluster_embeddings = resp.get("cluster_embeddings")

            # Fallback path: MaaS Whisper returns text-only (no segments, no
            # cluster_embeddings). Run pyannote LOCALLY on the audio to
            # recover speaker turns + embeddings, then proportionally split
            # the text across turns. Skipped automatically when STT already
            # returned segments (PhoWhisper path) or when HF_TOKEN is unset.
            if text and not segments and os.getenv("HF_TOKEN"):
                try:
                    from meeting.services.local_diarize import (
                        diarize_audio, split_text_proportional,
                    )
                    diarize = diarize_audio(audio_bytes)
                    if diarize["turns"]:
                        segments = split_text_proportional(text, diarize["turns"])
                        cluster_embeddings = (
                            cluster_embeddings or diarize["cluster_embeddings"]
                        )
                        logging.info(
                            f"[local pyannote] recovered {len(segments)} segments "
                            f"+ {len(cluster_embeddings)} cluster embeddings"
                        )
                except Exception as e:
                    logging.warning(
                        f"[local pyannote] failed (skipping diarize): {e}"
                    )

            return {
                "text": text,
                "segments": segments,
                "cluster_embeddings": cluster_embeddings,
                "chunked": False,
                "size_mb": round(original_size_mb, 1),
            }
        except requests.exceptions.HTTPError as e:
            # Whisper trả HTTP error status — include detail
            detail = f"Whisper HTTP error: {e}"
            if hasattr(e, "response") and e.response is not None:
                body = (e.response.text or "")[:500]
                detail += f" (status={e.response.status_code}, body={body!r})"
            logging.error(detail)
            raise HTTPException(status_code=502, detail=detail)
        except requests.exceptions.Timeout as e:
            logging.error(f"Whisper timeout: {e}")
            raise HTTPException(
                status_code=504,
                detail=f"Whisper timeout sau 60s. File có thể quá lớn → restart server (auto-chunk sẽ trigger ở > 24MB)",
            )
        except requests.exceptions.RequestException as e:
            logging.error(f"Whisper network error: {e}")
            raise HTTPException(status_code=502, detail=f"Whisper network error: {e}")

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
