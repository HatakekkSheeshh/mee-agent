"""SyncWSAdapter bridges the whisper_live backend's synchronous `.send()` calls
(made from a worker thread) onto an async Starlette WebSocket running on the
event loop. These tests run a real loop in a background thread and assert the
adapter dispatches str→send_text, bytes→send_bytes, dict→json, and close().
"""
from __future__ import annotations

import asyncio
import threading
import time

from src.ws_transcribe import SyncWSAdapter


class _FakeWS:
    def __init__(self):
        self.sent_text = []
        self.sent_bytes = []
        self.closed = False

    async def send_text(self, s):
        self.sent_text.append(s)

    async def send_bytes(self, b):
        self.sent_bytes.append(b)

    async def close(self):
        self.closed = True


def _loop_in_thread():
    loop = asyncio.new_event_loop()
    def _run():
        asyncio.set_event_loop(loop)

        def _wake_idle_selector():
            if loop.is_running():
                loop.call_later(0.001, _wake_idle_selector)

        loop.call_soon(_wake_idle_selector)
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # give the loop a moment to start spinning
    time.sleep(0.05)
    return loop


def test_adapter_dispatches_str_bytes_dict_and_close():
    loop = _loop_in_thread()
    try:
        ws = _FakeWS()
        adapter = SyncWSAdapter(ws, loop)

        adapter.send("hello")          # str → send_text
        adapter.send({"k": "v"})       # dict → json string → send_text
        adapter.send(b"\x00\x01\x02")  # bytes → send_bytes
        adapter.close()

        assert ws.sent_text[0] == "hello"
        assert '"k"' in ws.sent_text[1] and '"v"' in ws.sent_text[1]
        assert ws.sent_bytes == [b"\x00\x01\x02"]
        assert ws.closed is True
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_adapter_send_after_close_is_noop():
    loop = _loop_in_thread()
    try:
        ws = _FakeWS()
        adapter = SyncWSAdapter(ws, loop)
        adapter.close()
        adapter.send("ignored")  # must not raise, must not send
        assert ws.sent_text == []
    finally:
        loop.call_soon_threadsafe(loop.stop)
