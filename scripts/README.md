# Dev scripts

Khởi động từng phần của stack riêng biệt — tránh dual file-watcher ngốn CPU
khi để `run_meeting.py` tự spawn cả uvicorn + watchmedo + Celery.

## Cheat sheet (4 terminal)

```bash
# Tab 1 — Docker services (1 lần, để chạy nền)
scripts/db.sh
scripts/rabbitmq.sh

# Tab 2 — FastAPI backend (foreground, Ctrl+C để stop)
scripts/backend.sh

# Tab 3 — Celery worker (foreground, Ctrl+C để stop)
scripts/celery.sh

# Tab 4 — React FE (optional — vanilla FE đã được FastAPI serve sẵn)
scripts/frontend.sh
```

## Scripts

| Script         | Mục đích                                                                 |
|----------------|--------------------------------------------------------------------------|
| `db.sh`        | Start Postgres + Adminer container (idempotent)                          |
| `rabbitmq.sh`  | Start RabbitMQ container + đợi broker ready                              |
| `backend.sh`   | Start FastAPI **không kèm Celery** (`--no-celery`)                       |
| `celery.sh`    | Start Celery worker standalone, không watchmedo (1 file-watcher duy nhất)|
| `frontend.sh`  | Start React + Vite dev server (port 5173) — chỉ cần khi dev React FE     |
| `stop.sh`      | Kill mọi Python process (Docker services tự giữ chạy)                    |
| `status.sh`    | Liệt kê service nào đang chạy / dừng                                     |

## Env overrides

```bash
# Celery production-like (multi-process)
CELERY_POOL=prefork CELERY_CONCURRENCY=4 scripts/celery.sh

# Quiet logs
CELERY_LOGLEVEL=warning scripts/celery.sh
```

## Khi nào dùng script vs `run_meeting.py`

| Tình huống                          | Lệnh                            |
|-------------------------------------|---------------------------------|
| Dev local (nhanh, ít CPU)           | `scripts/{db,rabbitmq,backend,celery}.sh` (4 tab) |
| Demo / show off                     | `python run_meeting.py` (1 tab, auto-spawn cả 2)  |
| Test code mới ở `meeting/tasks.py`  | Ctrl+C `scripts/celery.sh` rồi chạy lại           |
| Test code mới ở `meeting/api/*.py`  | FastAPI auto-reload, không cần restart            |

## Troubleshooting

**Backend lag / CPU cao**: do `run_meeting.py` chạy 2 file-watcher song song.
Chuyển sang `scripts/backend.sh` + `scripts/celery.sh` (mỗi cái 1 watcher).

**Celery worker không thấy task mới**: nó cache `meeting/tasks.py` lúc start.
Ctrl+C `scripts/celery.sh` → chạy lại để pick up code mới.

**`docker exec mee-postgres ... not found`**: chạy `scripts/db.sh` trước.

**Port collision** (8000 / 5173 / 15672 đã dùng): `scripts/stop.sh` + check
`lsof -i :8000` xem process nào giữ.
