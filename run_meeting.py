#!/usr/bin/env python3
"""
Meeting Note Agent — Main Entry Point

Starts two servers:
1. WebSocket server (port 9091) — real-time transcription via MaaS
2. FastAPI HTTP server (port 8001) — web UI + MoM generation API

Usage:
    python run_meeting.py

    # With explicit config:
    python run_meeting.py --maas-url https://your-endpoint --maas-key your-key
"""
import argparse
import json
import logging
import os
import sys
import threading
import time

import numpy as np
import uvicorn
from websockets.sync.server import serve as ws_serve
from websockets.exceptions import ConnectionClosed

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class SimpleClientManager:
    def __init__(self, max_clients=4, max_connection_time=3600):
        self.clients = {}
        self.start_times = {}
        self.max_clients = max_clients
        self.max_connection_time = max_connection_time

    def add_client(self, websocket, client):
        self.clients[websocket] = client
        self.start_times[websocket] = time.time()

    def get_client(self, websocket):
        return self.clients.get(websocket)

    def remove_client(self, websocket):
        client = self.clients.pop(websocket, None)
        if client:
            client.cleanup()
        self.start_times.pop(websocket, None)

    def is_server_full(self, websocket, options):
        if len(self.clients) >= self.max_clients:
            wait_time = self._get_wait_time()
            websocket.send(json.dumps({
                "uid": options.get("uid", ""),
                "status": "WAIT",
                "message": wait_time,
            }))
            return True
        return False

    def is_client_timeout(self, websocket):
        start = self.start_times.get(websocket)
        if start and (time.time() - start) >= self.max_connection_time:
            client = self.clients.get(websocket)
            if client:
                client.disconnect()
            return True
        return False

    def _get_wait_time(self):
        if not self.start_times:
            return 0
        min_remaining = min(
            self.max_connection_time - (time.time() - t)
            for t in self.start_times.values()
        )
        return max(0, min_remaining / 60)


def start_whisper_server(args):
    from whisper_live.backend.maas_backend import ServeClientMaaS

    client_manager = SimpleClientManager(
        max_clients=args.max_clients,
        max_connection_time=args.max_connection_time,
    )

    def recv_audio(websocket):
        client = None
        try:
            logger.info("New client connected")
            options = json.loads(websocket.recv())

            if client_manager.is_server_full(websocket, options):
                websocket.close()
                return

            client = ServeClientMaaS(
                websocket=websocket,
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
                maas_base_url=args.maas_url,
                maas_api_key=args.maas_key,
                maas_model=args.maas_model,
            )
            client_manager.add_client(websocket, client)

            frame_count = 0
            while not client_manager.is_client_timeout(websocket):
                frame_data = websocket.recv()
                if frame_data == b"END_OF_AUDIO" or (
                    isinstance(frame_data, str) and "END_OF_AUDIO" in frame_data
                ):
                    logger.info("Received END_OF_AUDIO")
                    break
                if isinstance(frame_data, str):
                    continue
                client.add_frames(np.frombuffer(frame_data, dtype=np.float32))
                frame_count += 1

        except ConnectionClosed as e:
            logger.info(f"Connection closed: {e}")
        except json.JSONDecodeError:
            logger.error("Failed to decode client config JSON")
        except Exception as e:
            logger.error(f"Error in recv_audio: {e}", exc_info=True)
        finally:
            if client_manager.get_client(websocket):
                client_manager.remove_client(websocket)
            try:
                websocket.close()
            except Exception:
                pass

    logger.info(f"Starting WebSocket server on ws://0.0.0.0:{args.ws_port}")
    with ws_serve(
        recv_audio, "0.0.0.0", args.ws_port,
        ping_interval=30,
        ping_timeout=120,
        close_timeout=10,
        max_size=10 * 1024 * 1024,
    ) as ws_server:
        ws_server.serve_forever()


def start_http_server(args):
    from meeting.app import create_app

    output_dir = os.path.join(os.path.dirname(__file__), "output")
    app = create_app(output_dir=output_dir)

    logger.info(f"Starting Meeting Note HTTP server on http://0.0.0.0:{args.http_port}")
    uvicorn.run(app, host="0.0.0.0", port=args.http_port, log_level="info")


def main():
    parser = argparse.ArgumentParser(description="Meeting Note Agent")

    parser.add_argument("--maas-url", default=os.getenv("WHISPER_BASE_URL", ""))
    parser.add_argument("--maas-key", default=os.getenv("WHISPER_API_KEY", ""))
    parser.add_argument("--maas-model", default=os.getenv("WHISPER_MODEL", "openai/whisper-large-v3"))
    parser.add_argument("--ws-port", type=int, default=9091)
    parser.add_argument("--http-port", type=int, default=8001)
    parser.add_argument("--max-clients", type=int, default=4)
    parser.add_argument("--max-connection-time", type=int, default=7200)

    args = parser.parse_args()

    if not args.maas_url:
        logger.error("WHISPER_BASE_URL is required. Set via --maas-url or .env file.")
        sys.exit(1)

    ws_thread = threading.Thread(target=start_whisper_server, args=(args,), daemon=True)
    ws_thread.start()

    start_http_server(args)


if __name__ == "__main__":
    main()
