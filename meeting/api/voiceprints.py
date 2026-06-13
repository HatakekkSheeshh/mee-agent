"""Voiceprint enrollment + management.

Phase 2 (current): full enrollment pipeline.
  1. Accept WAV/WebM upload from /onboard/voice
  2. Decode → resample to 16 kHz mono via ffmpeg
  3. Run wespeaker (pyannote/wespeaker-voxceleb-resnet34-LM) → 256-d embedding
  4. Upsert into speaker_voiceprints with name = user.display_name
  5. Flip users.voice_enrolled = true

When the user later joins a meeting and pyannote produces SPEAKER_NN cluster
embeddings, the matcher cosine-searches speaker_voiceprints → auto-attaches
the user's real name to their cluster.

Auth: requires get_current_user → anonymous calls 401.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.auth import get_current_user
from meeting.db.base import get_session
from meeting.db.models import SpeakerVoiceprint, User


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voiceprints", tags=["voiceprints"])


def _output_dir() -> str:
    """Resolve OUTPUT_DIR from env, default to repo `output/`."""
    return os.getenv("OUTPUT_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output"
    )


def _convert_to_wav(src_path: str, dst_path: str) -> None:
    """ffmpeg-decode any input format (webm/opus/m4a/mp3/wav) → 16 kHz mono WAV.

    Wespeaker expects mono ≥ 8 kHz; pyannote pipelines default to 16 kHz.
    The `-y` flag overwrites without prompting; `-loglevel error` keeps stderr
    quiet for happy-path runs (we still capture+log on failure).
    """
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", src_path,
            "-ar", "16000",   # 16 kHz sample rate
            "-ac", "1",       # mono
            dst_path,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:500]}")


def _embed_audio(wav_path: str) -> list[float]:
    """Load pyannote wespeaker embedder and produce a single 256-d embedding
    for the whole clip. Embedder is cached after first call (see local_diarize)."""
    # Lazy import — pyannote is a heavy dep, defer until actually needed.
    from meeting.services.local_diarize import _ensure_loaded
    import soundfile as sf
    import torch

    _pipeline, embedder = _ensure_loaded()  # noqa: F841 — pipeline unused here

    audio_np, sr = sf.read(wav_path, dtype="float32")
    # pyannote shape contract: (channels, time). Ensure 2-D.
    if audio_np.ndim == 1:
        audio_np = audio_np[np.newaxis, :]
    else:
        audio_np = audio_np.T
    waveform = torch.from_numpy(np.ascontiguousarray(audio_np))
    audio_input = {"waveform": waveform, "sample_rate": int(sr)}

    # window="whole" → embedder returns a single vector for the entire clip.
    emb = embedder(audio_input)
    # Normalize across pyannote versions: result may be SlidingWindowFeature,
    # torch.Tensor, or numpy array.
    if hasattr(emb, "data"):
        emb = emb.data
    if hasattr(emb, "numpy"):
        emb = emb.numpy()
    emb = np.asarray(emb).flatten()
    if emb.size != 256:
        raise RuntimeError(
            f"Unexpected embedding dim: got {emb.size}, expected 256. "
            f"Check that pyannote/wespeaker-voxceleb-resnet34-LM is loaded."
        )
    return emb.tolist()


@router.post("/enroll")
async def enroll_voice(
    audio: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Upload enrollment audio → embed → store as the user's voiceprint.

    On success the user's `voice_enrolled` flag flips to true and a row is
    upserted into `speaker_voiceprints` keyed by (user_id, name). Calling
    this endpoint a second time re-records the voiceprint (newer embedding
    wins, sample_count increments).
    """
    content = await audio.read()
    size_kb = len(content) // 1024
    if size_kb < 4:
        raise HTTPException(
            status_code=400,
            detail=f"Audio quá ngắn ({size_kb} KB). Hãy ghi âm ít nhất 8 giây.",
        )

    # Persist the source file (webm or whatever the browser produced) under
    # output/voiceprints/<user_id>.<ext> — gives us a re-process audit trail.
    enrollment_dir = os.path.join(_output_dir(), "voiceprints")
    os.makedirs(enrollment_dir, exist_ok=True)
    ext = "webm"
    if audio.filename and "." in audio.filename:
        ext = audio.filename.rsplit(".", 1)[1][:8] or "webm"
    src_path = os.path.join(enrollment_dir, f"{user.id}.{ext}")
    with open(src_path, "wb") as f:
        f.write(content)
    logger.info(f"[voiceprints/enroll] saved {size_kb}KB for user={user.id}")

    # Decode + embed. Wrap so the user sees a meaningful error instead of 500.
    t0 = time.time()
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_tmp:
            wav_path = wav_tmp.name
        try:
            _convert_to_wav(src_path, wav_path)
            logger.info(f"[voiceprints/enroll] decoded → wav (ffmpeg) in {time.time()-t0:.1f}s")
            embedding = _embed_audio(wav_path)
            logger.info(
                f"[voiceprints/enroll] wespeaker embed done ({len(embedding)}-d) "
                f"total {time.time()-t0:.1f}s"
            )
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
    except FileNotFoundError as e:
        # Most common cause: ffmpeg binary missing on PATH.
        logger.error(f"[voiceprints/enroll] ffmpeg missing: {e}")
        raise HTTPException(
            500,
            "ffmpeg binary missing on server. Install: `sudo apt install ffmpeg`",
        )
    except Exception as e:
        logger.exception(f"[voiceprints/enroll] embed failed: {e}")
        raise HTTPException(500, f"Voice embedding failed: {e}")

    # Upsert voiceprint row keyed by (user_id, name). `name` is the user's
    # display_name so the matcher can directly attach this name to a meeting
    # cluster — no extra mapping table needed.
    voiceprint_name = (user.display_name or user.email.split("@")[0]).strip()
    stmt = select(SpeakerVoiceprint).where(
        SpeakerVoiceprint.user_id == user.id,
        SpeakerVoiceprint.name == voiceprint_name,
    )
    vp = (await session.execute(stmt)).scalar_one_or_none()
    if vp:
        # Re-enrollment — overwrite embedding with the fresher recording.
        vp.embedding = embedding
        vp.sample_count = (vp.sample_count or 0) + 1
        vp.last_seen_at = datetime.now(timezone.utc)
        logger.info(
            f"[voiceprints/enroll] updated voiceprint id={vp.id} "
            f"name={voiceprint_name} sample_count={vp.sample_count}"
        )
    else:
        vp = SpeakerVoiceprint(
            user_id=user.id,
            name=voiceprint_name,
            embedding=embedding,
            sample_count=1,
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(vp)
        await session.flush()
        logger.info(
            f"[voiceprints/enroll] created voiceprint id={vp.id} name={voiceprint_name}"
        )

    user.voice_enrolled = True
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    return {
        "ok": True,
        "user_id": str(user.id),
        "voice_enrolled": True,
        "voiceprint_id": str(vp.id),
        "voiceprint_name": voiceprint_name,
        "embedding_dim": len(embedding),
        "audio_size_kb": size_kb,
        "audio_path": src_path,
    }


@router.delete("/enrollment")
async def reset_enrollment(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Reset enrollment — flips voice_enrolled to false so the next /auth/me
    routes the user back to /onboard/voice. Does NOT delete the existing
    voiceprint row (keeps history); to fully delete, hit DELETE /voiceprint/{id}
    once that endpoint exists.
    """
    user.voice_enrolled = False
    await session.commit()
    return {"ok": True, "voice_enrolled": False}
