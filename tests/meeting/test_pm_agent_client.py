"""
Unit tests for the pm-agent A2A JSON-RPC client.

No live network: an httpx.MockTransport intercepts every POST and returns
recorded A2A v0.3 JSON-RPC response bodies. The wire shape mirrors what
pm-agent (a2a-sdk 1.0.2, enable_v0_3_compat=True) returns from the
non-streaming `message/send` — including the interrupted Task (state
"input-required") that carries an `approval_request` DataPart in the body.
"""
from __future__ import annotations

import json

import httpx
import pytest
import dotenv

from meeting.services.pm_agent_client import (
    PmAgentClient,
    PmAgentError,
    PmAgentResult,
)

URL = "https://pm-agent.example/a2a/"
KEY = "test-secret-key"


def _make_client(handler) -> PmAgentClient:
    """PmAgentClient wired to a MockTransport handler (no network)."""
    return PmAgentClient(url=URL, api_key=KEY, transport=httpx.MockTransport(handler))


def _completed_task(text: str = "Đã liệt kê 3 issue.") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "kind": "task",
            "id": "task-123",
            "contextId": "ctx-1",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": "a1",
                    "name": "reply",
                    "parts": [{"kind": "text", "text": text}],
                }
            ],
        },
    }


def _need_approval_task() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "kind": "task",
            "id": "task-456",
            "contextId": "ctx-1",
            "status": {
                "state": "input-required",
                "message": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "m1",
                    "parts": [{"kind": "text", "text": "Xác nhận tạo issue?"}],
                },
            },
            "artifacts": [
                {
                    "artifactId": "a2",
                    "name": "approval_request",
                    "parts": [
                        {
                            "kind": "data",
                            "data": {
                                "kind": "need_approval",
                                "message": "Xác nhận tạo issue?",
                                "issues": [
                                    {"actions": "CREATE", "subject": "Deploy v1"}
                                ],
                                "instructions": "reply approve|edit|reject",
                            },
                        }
                    ],
                }
            ],
        },
    }


def _need_more_info_task() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "kind": "task",
            "id": "task-789",
            "contextId": "ctx-1",
            "status": {
                "state": "input-required",
                "message": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "m2",
                    "parts": [{"kind": "text", "text": "Issue thuộc project nào?"}],
                },
            },
        },
    }


async def test_send_message_builds_jsonrpc_with_api_key():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completed_task())

    client = _make_client(handler)
    await client.send_message("liệt kê issue overdue")

    body = captured["body"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "message/send"
    msg = body["params"]["message"]
    assert msg["role"] == "user"
    text_parts = [p for p in msg["parts"] if p.get("kind") == "text"]
    assert text_parts and text_parts[0]["text"] == "liệt kê issue overdue"
    # X-API-KEY header present (case-insensitive header lookup)
    assert captured["headers"]["x-api-key"] == KEY


async def test_resume_includes_task_id_and_datapart():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completed_task())

    client = _make_client(handler)
    await client.send_message(
        "", task_id="task-456", context_id="ctx-1", data_part={"approval_action": "approve"}
    )

    msg = captured["body"]["params"]["message"]
    assert msg["taskId"] == "task-456"
    assert msg["contextId"] == "ctx-1"
    data_parts = [p for p in msg["parts"] if p.get("kind") == "data"]
    assert data_parts and data_parts[0]["data"] == {"approval_action": "approve"}


async def test_parse_completed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completed_task("Xong rồi nhé."))

    client = _make_client(handler)
    result = await client.send_message("liệt kê issue")

    assert isinstance(result, PmAgentResult)
    assert result.state == "completed"
    assert result.text == "Xong rồi nhé."
    assert result.need_approval is False
    assert result.issues is None
    assert result.task_id == "task-123"
    assert result.context_id == "ctx-1"


async def test_parse_need_approval():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_need_approval_task())

    client = _make_client(handler)
    result = await client.send_message("tạo issue deploy v1")

    assert result.state == "input_required"
    assert result.need_approval is True
    assert result.issues == [{"actions": "CREATE", "subject": "Deploy v1"}]
    assert result.task_id == "task-456"
    # human-readable text falls back to the status message
    assert "Xác nhận" in result.text


async def test_parse_need_more_info():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_need_more_info_task())

    client = _make_client(handler)
    result = await client.send_message("tạo issue")

    assert result.state == "input_required"
    assert result.need_approval is False
    assert result.issues is None
    assert "project nào" in result.text


async def test_http_error_raises_pmagenterror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _make_client(handler)
    with pytest.raises(PmAgentError):
        await client.send_message("anything")


async def test_jsonrpc_error_raises_pmagenterror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "internal"}},
        )

    client = _make_client(handler)
    with pytest.raises(PmAgentError):
        await client.send_message("anything")
