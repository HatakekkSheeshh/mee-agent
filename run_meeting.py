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
import re
import shutil
import subprocess
import sys
import threading
import time

import numpy as np
import uvicorn
from websockets.sync.server import serve as ws_serve
from websockets.exceptions import ConnectionClosed

try:
    from dotenv import load_dotenv
    load_dotenv(override=True, interpolate=False)
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
            # Post-record diarization: send the full buffered audio to
            # PhoWhisper to extract per-cluster embeddings, then POST them to
            # the backend. This is the live-record path's parity with file
            # upload — it's what lets voiceprint enrollment + cross-meeting
            # speaker recognition work after live recording. Runs in a
            # dedicated thread so the WebSocket close path isn't blocked
            # (PhoWhisper diarization on a 1-hour audio can take minutes).
            if client and hasattr(client, "post_record_diarize"):
                threading.Thread(
                    target=client.post_record_diarize,
                    daemon=False,  # let it finish even after WS closes
                    name=f"post-diarize-{getattr(client, 'client_uid', '?')}",
                ).start()
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


def _parse_db_url(url: str) -> dict:
    """Extract host/port/db/user from SQLAlchemy URL for display."""
    m = re.match(r"postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(\w+)", url or "")
    if not m:
        return {}
    return {
        "user": m.group(1),
        "password": m.group(2),
        "host": m.group(3),
        "port": m.group(4) or "5432",
        "db": m.group(5),
    }


def _docker_running(container_name: str) -> bool:
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", f"name={container_name}",
             "--filter", "status=running", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=2,
        )
        return container_name in out.stdout
    except Exception:
        return False


def _rabbitmq_reachable(timeout: float = 1.0) -> bool:
    """TCP probe to RabbitMQ broker (default localhost:5672). Used by the
    banner + main() to decide whether to spawn a Celery worker subprocess."""
    import socket
    host = os.getenv("RABBITMQ_HOST", "localhost")
    port = int(os.getenv("RABBITMQ_PORT", "5672"))
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def start_celery_worker() -> subprocess.Popen:
    """Spawn Celery worker as a child subprocess so MoM gen runs in the
    background. Lifetime tied to this script — main()'s finally block
    terminates it on Ctrl+C.

    concurrency=4 → 4 LLM gens in parallel per worker process. The real
    cap is MaaS rate limits / token quota, not local CPU.

    When `watchmedo` (from the `watchdog` package) is on PATH, wrap the
    worker so it auto-restarts on any .py change under meeting/. Celery
    doesn't pick up new @celery_app.task definitions until the worker
    process is restarted; without auto-reload, the dev loop is "edit
    tasks.py → manually kill + restart worker" which is annoying.
    """
    # Pool + concurrency tuned for dev on a 2-4 vCPU laptop:
    #   pool=solo (default)  → single-process worker, no fork overhead, fastest
    #                          startup, easiest to debug. One task at a time
    #                          (fine — MoM/clean are minute-scale, not high QPS).
    #   pool=prefork         → multi-process, set CELERY_CONCURRENCY for #workers.
    # Override via env to switch back to prefork for production-like behaviour.
    pool = os.getenv("CELERY_POOL", "solo")
    concurrency = os.getenv("CELERY_CONCURRENCY", "2")
    loglevel = os.getenv("CELERY_LOGLEVEL", "warning")
    celery_cmd = [
        sys.executable, "-m", "celery",
        "-A", "meeting.celery_app",
        "worker",
        f"--loglevel={loglevel}",
        f"--pool={pool}",
    ]
    # `concurrency` is only meaningful for multi-process pools.
    if pool not in ("solo", "threads"):
        celery_cmd.append(f"--concurrency={concurrency}")
    # Try PATH first, then fall back to the venv's bin dir alongside the
    # current Python — handles the case where the user runs the script
    # via `.venv/bin/python run_meeting.py` without activating the venv.
    watchmedo_bin = shutil.which("watchmedo") or shutil.which(
        "watchmedo", path=os.path.dirname(sys.executable)
    )
    if watchmedo_bin:
        cmd = [
            watchmedo_bin, "auto-restart",
            "--directory=meeting",
            "--pattern=*.py",
            "--recursive",
            "--signal=SIGTERM",
            "--",
            *celery_cmd,
        ]
        logger.info("Starting Celery worker (auto-reload via watchmedo)…")
    else:
        cmd = celery_cmd
        logger.info(
            "Starting Celery worker (no auto-reload — install watchdog "
            "for hot-reload: pip install watchdog)"
        )
    logger.info(f"  {' '.join(cmd)}")
    return subprocess.Popen(cmd)


class _C:
    """ANSI colors. Auto-disabled if stdout is not a tty (eg. piped to file)."""
    _enabled = sys.stdout.isatty()
    RESET   = "\033[0m"     if _enabled else ""
    BOLD    = "\033[1m"     if _enabled else ""
    DIM     = "\033[2m"     if _enabled else ""
    RED     = "\033[31m"    if _enabled else ""
    GREEN   = "\033[32m"    if _enabled else ""
    YELLOW  = "\033[33m"    if _enabled else ""
    BLUE    = "\033[34m"    if _enabled else ""
    MAGENTA = "\033[35m"    if _enabled else ""
    CYAN    = "\033[36m"    if _enabled else ""
    GRAY    = "\033[90m"    if _enabled else ""
    BRIGHT_GREEN = "\033[92m" if _enabled else ""


def _print_startup_banner(http_port: int, ws_port: int) -> None:
    c = _C
    db = _parse_db_url(os.getenv("DATABASE_URL", ""))
    pg_up = _docker_running("mee-postgres")
    adminer_up = _docker_running("mee-adminer")
    rabbit_up = _rabbitmq_reachable()

    def status(running: bool) -> str:
        if running:
            return f"{c.GREEN}● running{c.RESET}"
        return f"{c.RED}● stopped{c.RESET} {c.DIM}— run: docker compose up -d{c.RESET}"

    def url(text: str) -> str:
        return f"{c.BLUE}{text}{c.RESET}"

    def hint(text: str) -> str:
        return f"{c.DIM}{text}{c.RESET}"

    def method(verb: str) -> str:
        color = c.YELLOW if verb == "POST" else c.GREEN if verb == "GET" else c.MAGENTA
        return f"{color}{verb:<6}{c.RESET}"

    line = f"{c.GRAY}─────────────────────────────────────────────────────────────────────────{c.RESET}"

    banner = f"""
{line}
  {c.BOLD}{c.BRIGHT_GREEN}🎙  Mee Meeting Agent{c.RESET}
{line}

  {c.BOLD}{c.CYAN}HTTP API + Frontend{c.RESET}
    {url(f'http://localhost:{http_port}/')}
    {url(f'http://localhost:{http_port}/docs')}        {hint('(Swagger — test API)')}
    {url(f'http://localhost:{http_port}/redoc')}       {hint('(ReDoc — read API)')}

  {c.BOLD}{c.CYAN}WebSocket{c.RESET} {hint('(Whisper realtime STT)')}
    {url(f'ws://localhost:{ws_port}')}

  {c.BOLD}{c.CYAN}Postgres{c.RESET}    {status(pg_up)}
    {hint('host=')}{db.get('host', '?')}:{db.get('port', '?')}   {hint('db=')}{db.get('db', '?')}   {hint('user=')}{db.get('user', '?')}
    {c.DIM}docker exec -it mee-postgres psql -U {db.get('user', 'mee')} -d {db.get('db', 'mee')}{c.RESET}

  {c.BOLD}{c.CYAN}Adminer GUI{c.RESET} {status(adminer_up)}
    {url('http://localhost:8080/')}
    {hint(f"Login: System=PostgreSQL  Server=postgres  User={db.get('user', 'mee')}")}

  {c.BOLD}{c.CYAN}RabbitMQ + Celery{c.RESET} {hint('(background MoM gen)')} {status(rabbit_up)}
    {hint('AMQP:')}  amqp://localhost:5672/
    {hint('UI:')}    {url('http://localhost:15672/')}  {hint('(login: mee / mee_dev_password)')}
    {hint('Worker:')} {'auto-spawned' if rabbit_up else 'skipped — broker down'} {hint('(falls back to inline asyncio.to_thread)')}

  {c.BOLD}{c.CYAN}Key endpoints{c.RESET} {hint('(DB-backed, Phase A+B)')}
    {method('POST')} /api/meetings                       {hint('create meeting')}
    {method('GET')} /api/meetings                       {hint('list meetings')}
    {method('POST')} /api/meetings/{{id}}/recordings       {hint('start recording')}
    {method('POST')} /api/recordings/{{id}}/segments       {hint('add segment')}
    {method('POST')} /api/meetings/{{id}}/generate-mom     {hint('LangGraph MoM gen')}
    {method('POST')} /api/transcribe                     {hint('Whisper file upload')}

  {c.BOLD}{c.CYAN}Docs{c.RESET} {hint('(Obsidian vault)')}
    {c.DIM}/home/lap15466/greennode/GreenNode/Meeting Agent/{c.RESET}
    {hint('README.md  ·  Progress Log  ·  Phase A/B Setup')}

{line}
"""
    print(banner, flush=True)
    if not pg_up:
        print(
            f"{c.YELLOW}⚠ WARNING:{c.RESET} Postgres không chạy — endpoints DB sẽ lỗi. "
            f"Chạy: {c.BOLD}docker compose up -d postgres{c.RESET}\n",
            flush=True,
        )


def start_http_server(args):
    _print_startup_banner(args.http_port, args.ws_port)
    logger.info(f"Starting Meeting Note HTTP server on http://0.0.0.0:{args.http_port}")
    # Use factory + import string so uvicorn's reloader can re-import on
    # file changes. create_app() defaults output_dir to <repo>/output when
    # None is passed, which matches the previous behavior.
    uvicorn.run(
        "meeting.app:create_app",
        host="0.0.0.0",
        port=args.http_port,
        log_level="info",
        reload=True,
        factory=True,
        reload_dirs=[os.path.join(os.path.dirname(__file__), "meeting")],
    )


def main():
    parser = argparse.ArgumentParser(description="Meeting Note Agent")

    parser.add_argument("--maas-url", default=os.getenv("WHISPER_BASE_URL", ""))
    parser.add_argument("--maas-key", default=os.getenv("WHISPER_API_KEY", ""))
    parser.add_argument("--maas-model", default=os.getenv("WHISPER_MODEL", "openai/whisper-large-v3"))
    parser.add_argument("--ws-port", type=int, default=9091)
    parser.add_argument("--http-port", type=int, default=8001)
    parser.add_argument("--max-clients", type=int, default=4)
    parser.add_argument("--max-connection-time", type=int, default=7200)
    parser.add_argument(
        "--no-celery", action="store_true",
        help="Skip spawning the Celery worker (eg. when running it manually "
             "in a separate terminal for hot-reload during task development)",
    )

    args = parser.parse_args()

    if not args.maas_url:
        logger.error("WHISPER_BASE_URL is required. Set via --maas-url or .env file.")
        sys.exit(1)

    # WebSocket server in a daemon thread (dies with main process).
    ws_thread = threading.Thread(target=start_whisper_server, args=(args,), daemon=True)
    ws_thread.start()

    # Celery worker as a separate subprocess. Skipped when broker is down
    # (MoM gen falls back to inline asyncio.to_thread, see endpoint) or when
    # --no-celery is passed (eg. running worker manually for hot-reload).
    celery_proc = None
    if not args.no_celery:
        if _rabbitmq_reachable():
            celery_proc = start_celery_worker()
        else:
            logger.warning(
                "⚠ RabbitMQ broker not reachable on localhost:5672 — skipping "
                "Celery worker. MoM gen will use inline fallback. To enable "
                "background tasks: `docker compose up -d rabbitmq` then "
                "restart this script."
            )

    try:
        start_http_server(args)
    finally:
        if celery_proc is not None:
            logger.info("Stopping Celery worker…")
            celery_proc.terminate()
            try:
                celery_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Celery worker didn't exit cleanly — killing.")
                celery_proc.kill()


if __name__ == "__main__":
    main()
