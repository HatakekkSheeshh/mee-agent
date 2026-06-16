"""Live-transcription WebSocket, mounted into the FastAPI app on the single app
port (8080 in prod via main.py, 8002 in dev via run_meeting) so AgentBase's
single-port runtime serves realtime STT alongside the HTTP API — no separate
:9091 server needed.

The whisper_live `ServeClientMaaS` backend is written against the *synchronous*
`websockets` API: it calls `ws.send(str)` from a background worker thread.
FastAPI/Starlette WebSockets are async. `SyncWSAdapter` bridges the two — it
exposes the sync `.send()/.close()` the backend expects and marshals each call
onto the event loop via `run_coroutine_threadsafe`. The async endpoint owns the
receive loop (config message, then float32 audio frames, then END_OF_AUDIO).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time

import numpy as np
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)


class SyncWSAdapter:
    """Sync facade over an async Starlette WebSocket.

    The whisper_live backend pushes results with a blocking `ws.send()` from its
    own thread. We hop each call onto the event loop thread-safely. Once a send
    or close fails (client gone / loop stopped) we latch `_closed` so the worker
    stops hammering a dead socket.
    """

    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self._ws = ws
        self._loop = loop
        self._closed = False

    def send(self, data) -> None:
        if self._closed:
            return
        if isinstance(data, (bytes, bytearray)):
            coro = self._ws.send_bytes(bytes(data))
        elif isinstance(data, str):
            coro = self._ws.send_text(data)
        else:
            coro = self._ws.send_text(json.dumps(data))
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=15)
        except Exception as e:
            self._closed = True
            logger.debug("[ws] send failed (client likely gone): %s", e)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop).result(timeout=5)
        except Exception:
            pass


class _ClientManager:
    """Caps concurrent live sessions + enforces a max connection duration.
    Keyed by the per-connection adapter (one client per socket)."""

    def __init__(self, max_clients: int, max_connection_time: int) -> None:
        self.clients: dict = {}
        self.start_times: dict = {}
        self.max_clients = max_clients
        self.max_connection_time = max_connection_time

    def add_client(self, key, client) -> None:
        self.clients[key] = client
        self.start_times[key] = time.time()

    def get_client(self, key):
        return self.clients.get(key)

    def remove_client(self, key) -> None:
        client = self.clients.pop(key, None)
        if client:
            try:
                client.cleanup()
            except Exception:
                logger.debug("[ws] client cleanup failed", exc_info=True)
        self.start_times.pop(key, None)

    def is_full(self) -> bool:
        return len(self.clients) >= self.max_clients

    def is_timeout(self, key) -> bool:
        start = self.start_times.get(key)
        return bool(start and (time.time() - start) >= self.max_connection_time)


def register_ws_route(app: FastAPI) -> None:
    """Mount the realtime-STT WebSocket at /ws. MaaS Whisper config comes from
    the same env vars the HTTP transcribe path uses (WHISPER_BASE_URL/KEY/MODEL).

    Must be called BEFORE the catch-all static mount so the upgrade request to
    /ws is routed here, not swallowed by StaticFiles.
    """
    maas_url = os.getenv("WHISPER_BASE_URL", "")
    maas_key = os.getenv("WHISPER_API_KEY", "")
    maas_model = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")
    manager = _ClientManager(
        max_clients=int(os.getenv("WS_MAX_CLIENTS", "4")),
        max_connection_time=int(os.getenv("WS_MAX_CONNECTION_TIME", "7200")),
    )

    @app.websocket("/ws")
    async def ws_transcribe(websocket: WebSocket) -> None:
        # Lazy import — keeps numpy/whisper_live off the import path for non-WS
        # deployments and matches run_meeting's deferred import.
        from whisper_live.backend.maas_backend import ServeClientMaaS

        await websocket.accept()
        loop = asyncio.get_running_loop()
        adapter = SyncWSAdapter(websocket, loop)
        client = None
        try:
            options = json.loads(await websocket.receive_text())

            if manager.is_full():
                adapter.send({"uid": options.get("uid", ""), "status": "WAIT", "message": 0})
                await websocket.close()
                return

            # ServeClientMaaS spins up a worker thread that pushes partial/final
            # transcripts back via adapter.send(). It only ever *sends* — the
            # receive loop below feeds it audio via add_frames().
            client = ServeClientMaaS(
                websocket=adapter,
                task=options.get("task", "transcribe"),
                language=options.get("language"),
                client_uid=options.get("uid"),
                initial_prompt=options.get("initial_prompt"),
                vad_parameters=options.get("vad_parameters"),
                use_vad=options.get("use_vad", True),
                send_last_n_segments=options.get("send_last_n_segments", 10),
                no_speech_thresh=options.get("no_speech_thresh", 0.45),
                clip_audio=options.get("clip_audio", False),
                same_output_threshold=options.get("same_output_threshold", 10),
                maas_base_url=maas_url,
                maas_api_key=maas_key,
                maas_model=maas_model,
            )
            manager.add_client(adapter, client)

            while not manager.is_timeout(adapter):
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    if data == b"END_OF_AUDIO":
                        break
                    client.add_frames(np.frombuffer(data, dtype=np.float32))
                    continue
                text = msg.get("text")
                if text and "END_OF_AUDIO" in text:
                    break
        except WebSocketDisconnect:
            logger.info("[ws] client disconnected")
        except json.JSONDecodeError:
            logger.error("[ws] failed to decode client config JSON")
        except Exception:
            logger.exception("[ws] transcription error")
        finally:
            # Post-record diarization on a non-daemon thread so it survives the
            # socket close (parity with the file-upload path). Start it BEFORE
            # cleanup, matching the legacy recv_audio ordering.
            if client is not None and hasattr(client, "post_record_diarize"):
                threading.Thread(
                    target=client.post_record_diarize,
                    daemon=False,
                    name=f"post-diarize-{getattr(client, 'client_uid', '?')}",
                ).start()
            manager.remove_client(adapter)
            adapter.close()
