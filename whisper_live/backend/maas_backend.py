"""
MaaS (Model-as-a-Service) backend for WhisperLive.
Sends audio to a remote OpenAI-compatible Whisper API (e.g., VNGCloud MaaS)
instead of running local inference.
"""
import io
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

import numpy as np
import requests
import soundfile as sf

from whisper_live.backend.base import ServeClientBase


@dataclass
class MaaSSegment:
    """Mimics faster-whisper Segment interface for compatibility with ServeClientBase."""
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0


class ServeClientMaaS(ServeClientBase):
    """
    WhisperLive backend that transcribes via a remote OpenAI-compatible Whisper API.
    Compatible with VNGCloud MaaS, OpenAI, and other providers.

    Key differences from faster-whisper backend:
    - Overrides speech_to_text() to accumulate longer audio chunks (10-30s)
      before making API calls, giving Whisper enough context
    - Includes audio overlap between chunks for continuity
    - Passes previous transcription as prompt for better accuracy
    """

    # Minimum seconds of audio before making an API call
    MIN_CHUNK_DURATION = 8
    # Seconds of overlap with previous chunk for context
    OVERLAP_DURATION = 2

    # Common Whisper hallucinations in Vietnamese (YouTube training data leakage)
    HALLUCINATION_PATTERNS = [
        # YouTube outros
        "subscribe", "la la school", "ghiền mì gõ", "không bỏ lỡ",
        "video hấp dẫn", "cảm ơn các bạn đã theo dõi", "nhớ like",
        "nhấn chuông", "đăng ký kênh", "xin chào các bạn", "like share",
        "kênh youtube", "theo dõi kênh", "bấm nút", "phụ đề",
        "thuyết minh", "vietsub",
        # Video closing phrases — common on silence/noise
        "hẹn gặp lại các bạn", "hẹn gặp lại các bạn trong",
        "video tiếp theo", "video sau", "tập tiếp theo",
        "trong những video tiếp theo", "trong tập tiếp theo",
        "cảm ơn các bạn đã xem", "cảm ơn các bạn đã đón xem",
        # Vague hooks that Whisper inserts on silence
        "các bạn có thể nhìn thấy", "bạn có thể nhìn thấy",
        "các bạn thấy", "như các bạn thấy",
        # English YouTube fillers
        "thanks for watching", "see you next time", "see you in the next video",
    ]

    # Regex patterns for hallucinated single characters/noise at start of segment
    import re
    _NOISE_PREFIX_RE = re.compile(r"^[ĐđÔôƠơÊê]\s*$|^[A-ZĐ]\.$")
    # Pure-filler segments (single particle ± punctuation), no real speech
    _FILLER_ONLY_RE = re.compile(
        r"^[\s.,!?…]*(?:à|ờ|ừm?|ư|ơi|ah|uh|um|mm|hmm|ah\.?|eh|oh)[\s.,!?…]*$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        websocket,
        task="transcribe",
        language=None,
        client_uid=None,
        initial_prompt=None,
        vad_parameters=None,
        use_vad=True,
        send_last_n_segments=10,
        no_speech_thresh=0.45,
        clip_audio=False,
        same_output_threshold=7,
        maas_base_url=None,
        maas_api_key=None,
        maas_model=None,
        translation_queue=None,
    ):
        super().__init__(
            client_uid,
            websocket,
            send_last_n_segments,
            no_speech_thresh,
            clip_audio,
            same_output_threshold,
            translation_queue,
        )

        self.language = language
        self.task = task
        self.initial_prompt = initial_prompt or ""
        self.vad_parameters = vad_parameters or {"threshold": 0.5}
        self.use_vad = use_vad

        self.maas_base_url = (maas_base_url or os.getenv("WHISPER_BASE_URL", "")).rstrip("/")
        self.maas_api_key = maas_api_key or os.getenv("WHISPER_API_KEY", "")
        self.maas_model = maas_model or os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")

        if not self.maas_base_url:
            raise ValueError("MaaS base URL is required. Set WHISPER_BASE_URL env var.")

        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.maas_api_key}"})

        # Track previous transcription for continuity prompt
        self.prev_transcription = ""
        # Track absolute time position
        self.absolute_time = 0.0
        # All audio received so far (for proper chunking)
        self.all_audio = np.array([], dtype=np.float32)
        self.audio_lock = threading.Lock()
        # Position of last processed audio (in samples)
        self.processed_samples = 0

        logging.info(f"MaaS backend: {self.maas_base_url} model={self.maas_model}")

        # Start transcription thread
        self.trans_thread = threading.Thread(target=self.speech_to_text)
        self.trans_thread.start()
        self.websocket.send(
            json.dumps({
                "uid": self.client_uid,
                "message": self.SERVER_READY,
                "backend": "maas",
            })
        )

    def add_frames(self, frame_np):
        """Accumulate audio frames in our own buffer (bypass base class 45s limit)."""
        with self.audio_lock:
            self.all_audio = np.concatenate((self.all_audio, frame_np.copy()))
            total_dur = len(self.all_audio) / self.RATE
            unproc = (len(self.all_audio) - self.processed_samples) / self.RATE
            if int(total_dur) % 5 == 0 and int(total_dur) != getattr(self, '_last_log', 0):
                self._last_log = int(total_dur)
                logging.info(f"Audio buffer: {total_dur:.1f}s total, {unproc:.1f}s unprocessed, {len(frame_np)} new samples")

    def speech_to_text(self):
        """
        Custom transcription loop for MaaS backend.

        Instead of using the base class loop (which sends tiny chunks),
        this accumulates at least MIN_CHUNK_DURATION seconds before
        making an API call, and includes overlap for context.
        """
        while True:
            if self.exit:
                logging.info("Exiting MaaS speech_to_text thread")
                break

            with self.audio_lock:
                total_samples = len(self.all_audio)

            unprocessed = total_samples - self.processed_samples
            unprocessed_duration = unprocessed / self.RATE

            if unprocessed_duration < self.MIN_CHUNK_DURATION:
                time.sleep(0.5)
                continue

            logging.info(f"Processing chunk: {unprocessed_duration:.1f}s unprocessed, absolute_time={self.absolute_time:.1f}s")

            # Get audio chunk: overlap from previous + new audio
            with self.audio_lock:
                overlap_samples = int(self.OVERLAP_DURATION * self.RATE)
                chunk_start = max(0, self.processed_samples - overlap_samples)
                chunk = self.all_audio[chunk_start:total_samples].copy()

            chunk_duration = len(chunk) / self.RATE

            try:
                # Skip silent chunks before calling Whisper — prevents YouTube-style
                # hallucinations ("Hẹn gặp lại...", "subscribe", etc.) on dead air.
                if self._is_silent(chunk):
                    logging.info(f"Skipping silent chunk ({chunk_duration:.1f}s, RMS too low)")
                    self.processed_samples = total_samples
                    self.absolute_time += unprocessed_duration
                    time.sleep(0.5)
                    continue

                text = self._call_whisper_api(chunk)

                if text is None:
                    # Hallucination or empty — skip this chunk
                    self.processed_samples = total_samples
                    self.absolute_time += unprocessed_duration
                    time.sleep(0.5)
                    continue

                # Remove repeated phrases within this segment
                text = self._remove_phrase_duplicates(text)

                # Remove overlap text: the overlap portion may produce duplicate text
                # at the beginning. We use the prompt mechanism to handle this,
                # but also trim if the text starts with the previous transcription.
                if self.prev_transcription and text.startswith(self.prev_transcription[:50]):
                    text = text[len(self.prev_transcription):].strip()

                if not text.strip():
                    self.processed_samples = total_samples
                    self.absolute_time += unprocessed_duration
                    continue

                # Create completed segment
                start_time = self.absolute_time
                end_time = self.absolute_time + unprocessed_duration
                completed_segment = self.format_segment(
                    start_time, end_time, text.strip(), completed=True
                )
                self.transcript.append(completed_segment)

                # Send to client
                segments = self.prepare_segments(last_segment=None)
                if segments:
                    self.send_transcription_to_client(segments)

                # Advance position
                self.processed_samples = total_samples
                self.absolute_time += unprocessed_duration
                self.prev_transcription = text.strip()

                # Memory management: trim processed audio (keep last 30s for overlap)
                keep_samples = int(30 * self.RATE)
                with self.audio_lock:
                    if len(self.all_audio) > keep_samples * 2:
                        trim = len(self.all_audio) - keep_samples
                        self.all_audio = self.all_audio[trim:]
                        self.processed_samples = max(0, self.processed_samples - trim)

            except Exception as e:
                logging.error(f"MaaS transcription error: {e}", exc_info=True)
                self.processed_samples = total_samples
                self.absolute_time += unprocessed_duration
                time.sleep(1)

    def _call_whisper_api(self, audio_chunk: np.ndarray) -> str | None:
        """
        Send audio chunk to Whisper API and return text.

        Returns None if hallucination detected or API fails.
        """
        wav_bytes = self._audio_to_wav_bytes(audio_chunk)

        data = {"model": self.maas_model, "response_format": "json"}
        if self.language:
            data["language"] = self.language

        # Build prompt: initial context + last transcription for continuity
        prompt_parts = []
        if self.initial_prompt:
            prompt_parts.append(self.initial_prompt)
        if self.prev_transcription:
            # Last 200 chars of previous transcription for context
            prompt_parts.append(self.prev_transcription[-200:])
        if prompt_parts:
            data["prompt"] = " ".join(prompt_parts)

        # Retry up to 3 times
        for attempt in range(3):
            try:
                resp = self.session.post(
                    f"{self.maas_base_url}/v1/audio/transcriptions",
                    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                    data=data,
                    timeout=60,
                )
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "").strip()
                logging.info(f"Whisper API response: '{text[:100]}'")

                if self._is_hallucination(text):
                    logging.info(f"Filtered hallucination: '{text[:80]}'")
                    return None

                return text

            except requests.exceptions.RequestException as e:
                logging.warning(f"MaaS API attempt {attempt+1}/3: {e}")
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))

        logging.error("MaaS API failed after 3 attempts")
        return None

    def _is_hallucination(self, text: str) -> bool:
        """Check if text is a Whisper hallucination."""
        if not text or not text.strip():
            return True
        stripped = text.strip()
        lower = stripped.lower()
        # Length check ignoring punctuation/whitespace
        bare = "".join(c for c in lower if c.isalpha())
        if len(bare) <= 2:
            return True
        # Single noise character (e.g. lone "Đ", "Ô", "Ê")
        if self._NOISE_PREFIX_RE.match(stripped):
            return True
        # Pure filler particle ("à", "ờ.", "ừm", "à!", etc.)
        if self._FILLER_ONLY_RE.match(stripped):
            return True
        for pattern in self.HALLUCINATION_PATTERNS:
            if pattern in lower:
                return True
        words = lower.split()
        if len(words) >= 3 and len(set(words)) == 1:
            return True
        return False

    def _remove_phrase_duplicates(self, text: str) -> str:
        """
        Remove repeated phrases within a single transcription segment.
        E.g. "hello hello hello world" -> "hello world"
        Handles cases where Whisper repeats a phrase 3+ times consecutively.
        """
        import re
        # Detect any phrase of 2-8 words repeated 3+ times consecutively
        pattern = re.compile(r'\b((?:\S+ ){1,8}\S+)(?:\s+\1){2,}', re.IGNORECASE)
        cleaned = pattern.sub(r'\1', text)
        # Also handle single-word repetition (already caught by hallucination for all-same,
        # but catch partial: "anh anh anh nói" -> "anh nói")
        cleaned = re.sub(r'\b(\S+)(\s+\1){2,}\b', r'\1', cleaned)
        return cleaned.strip()

    def _is_silent(self, audio_chunk: np.ndarray, rms_threshold: float = 0.005) -> bool:
        """RMS-based silence detector. Whisper hallucinates badly on silent input."""
        if audio_chunk.size == 0:
            return True
        rms = float(np.sqrt(np.mean(audio_chunk.astype(np.float64) ** 2)))
        return rms < rms_threshold

    def _audio_to_wav_bytes(self, audio_np: np.ndarray) -> bytes:
        """Convert float32 numpy audio array to WAV bytes."""
        buf = io.BytesIO()
        sf.write(buf, audio_np, self.RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()
