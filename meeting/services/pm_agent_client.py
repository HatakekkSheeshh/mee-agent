"""
pm-agent A2A client — a thin async JSON-RPC wrapper over httpx.

We deliberately do NOT use `a2a-sdk`: that SDK (1.0.2) pins `protobuf<6`,
a constraint the meeting repo should not inherit for the sake of two RPC
calls. Instead we speak the A2A v0.3 JSON-RPC wire format directly. pm-agent
runs a2a-sdk 1.0.2 with `enable_v0_3_compat=True`, which accepts this format.

## message/send is blocking and returns interrupts in the body (verified)

The critical question (design Open Q #3): does the non-streaming `message/send`
return an interrupted Task (HITL approval) in the HTTP response body, or only
over SSE? Verified against the a2a-sdk it runs:

  - pm-agent's AgentExecutor.execute() runs the LangGraph via svc.handle(),
    which RETURNS when the graph hits interrupt(); execute() then enqueues a
    TaskStatusUpdateEvent(state=input-required) (+ an `approval_request`
    DataPart artifact for write ops) and returns — it does NOT block in-process
    waiting for a resume.
  - a2a-sdk's DefaultRequestHandler.on_message_send drains the event queue via
    ResultAggregator.consume_and_break_on_interrupt. That helper only *breaks
    early* on `auth-required`; for `input-required` the producer has already
    finished, so the loop ends naturally and it returns the aggregated Task —
    in state "input-required", carrying the approval DataPart.

=> We use `message/send` (non-streaming) and parse INPUT_REQUIRED from the body.
   No SSE / `message/stream` is needed.

Wire shapes (A2A v0.3 / a2a-sdk JSON spec):
  request:  {"jsonrpc":"2.0","id":..,"method":"message/send",
             "params":{"message":{"kind":"message","role":"user","messageId":..,
                                   "parts":[{"kind":"text","text":..}],
                                   "taskId":..?}}}
  resume:   same, with message.taskId set + an extra {"kind":"data","data":{..}} part
  response: {"jsonrpc":"2.0","id":..,"result":<Task>}  (or {"error":{..}})
  Task:     {"kind":"task","id":<task_id>,"contextId":..,
             "status":{"state":"completed|input-required|failed|..","message":{parts:[{text}]}},
             "artifacts":[{"name":..,"parts":[{"kind":"text","text":..} | {"kind":"data","data":{..}}]}]}
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

logger = logging.getLogger(__name__)

PmState = Literal["completed", "failed", "input_required", "working"]

# a2a TaskState (kebab-case) → our normalized state.
_STATE_MAP: dict[str, PmState] = {
    "completed": "completed",
    "failed": "failed",
    "rejected": "failed",
    "canceled": "failed",
    "cancelled": "failed",
    "input-required": "input_required",
    "auth-required": "input_required",
    "working": "working",
    "submitted": "working",
}

_DEFAULT_TIMEOUT = float(os.getenv("PM_AGENT_TIMEOUT", "60"))


@dataclass(frozen=True)
class PmAgentResult:
    """Framework-agnostic result so the graph never sees JSON-RPC/protobuf."""

    task_id: str
    state: PmState
    text: str
    need_approval: bool
    issues: Optional[list[dict]]
    # A2A conversation context. MUST be echoed back (with task_id) on every
    # follow-up/resume, or the server's TaskManager raises a -32603
    # "Context in event doesn't match TaskManager" error.
    context_id: Optional[str] = None


class PmAgentError(Exception):
    """Raised on transport error, non-2xx, or a JSON-RPC error envelope."""


class PmAgentClient:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not url:
            raise PmAgentError("PM_AGENT_URL is not configured")
        # A2A JSON-RPC posts to the `/a2a/` base. Ensure the trailing slash so
        # we don't hit a 307/308 redirect (this httpx client does not follow
        # redirects, and a cross-host redirect would also drop the auth header).
        self._url = url if url.endswith("/") else url + "/"
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport  # injected in tests (httpx.MockTransport)

    # ─── public API ──────────────────────────────────────────────

    async def send_message(
        self,
        text: str,
        *,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        data_part: Optional[dict] = None,
        bearer: Optional[str] = None,
    ) -> PmAgentResult:
        """One `message/send` call. Idempotent per invocation (never auto-resumes).

        `bearer`, when set, is the per-request identity token (the logged-in
        user's OID for pm-agent's direct-oid path, or a Graph JWT later). It
        overrides the static api_key on the Authorization header so each user's
        request carries their own identity. When None, falls back to api_key.
        """
        parts: list[dict] = [{"kind": "text", "text": text}]
        if data_part is not None:
            parts.append({"kind": "data", "data": data_part})

        message: dict = {
            "kind": "message",
            "role": "user",
            "messageId": str(uuid.uuid4()),
            "parts": parts,
        }
        if task_id:
            message["taskId"] = task_id
        # Echo the conversation context on resume so the server's TaskManager
        # matches it to the existing task (else -32603 context mismatch).
        if context_id:
            message["contextId"] = context_id

        result = await self._rpc("message/send", {"message": message}, bearer=bearer)
        return self._parse_result(result)

    async def cancel(self, task_id: str) -> None:
        """Best-effort `tasks/cancel`. Swallows errors (cleanup, not critical)."""
        try:
            await self._rpc("tasks/cancel", {"id": task_id})
        except PmAgentError:
            logger.warning("pm-agent cancel failed for task_id=%s", task_id)

    # ─── transport ───────────────────────────────────────────────

    async def _rpc(self, method: str, params: dict, *, bearer: Optional[str] = None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        # The deployed agentbase endpoint authenticates via
        # `Authorization: Bearer <token>` (verified: X-API-KEY → 401, Bearer →
        # 200). A locally-run pm-agent uses X-API-KEY = API_SEC_KEY. Send both
        # so the client works against either; the server reads whichever it wants.
        #
        # `bearer` (per-request user identity — e.g. the logged-in user's OID)
        # takes priority on Authorization so each user's request reaches
        # pm-agent's direct-oid path as themselves; falls back to api_key.
        headers = {
            "Authorization": f"Bearer {bearer or self._api_key}",
            "X-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise PmAgentError(f"pm-agent transport error: {e}") from e

        if resp.status_code >= 400:
            raise PmAgentError(
                f"pm-agent HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise PmAgentError(f"pm-agent returned non-JSON body: {e}") from e

        if data.get("error"):
            raise PmAgentError(f"pm-agent JSON-RPC error: {data['error']}")
        result = data.get("result")
        if result is None:
            raise PmAgentError("pm-agent response missing 'result'")
        return result

    # ─── parsing ─────────────────────────────────────────────────

    def _parse_result(self, result: dict) -> PmAgentResult:
        # A non-streaming send may return a bare Message instead of a Task.
        if result.get("kind") == "message":
            return PmAgentResult(
                task_id=result.get("taskId") or "",
                state="completed",
                text=_strip_slash_hint(_parts_text(result.get("parts"))),
                need_approval=False,
                issues=None,
                context_id=result.get("contextId"),
            )

        task_id = result.get("id") or result.get("taskId") or ""
        status = result.get("status") or {}
        state = _STATE_MAP.get(status.get("state", ""), "failed")
        artifacts = result.get("artifacts") or []

        need_approval, issues = _detect_approval(artifacts)

        # Prefer text artifacts; fall back to the status message text.
        # Strip pm-agent's "/add … /cancel" slash-command hint — the chat UI
        # replaces it with a reply input + Gửi/Hủy buttons.
        text = _strip_slash_hint(_artifacts_text(artifacts) or _status_text(status))

        return PmAgentResult(
            task_id=task_id,
            state=state,
            text=text,
            need_approval=need_approval,
            issues=issues,
            context_id=result.get("contextId"),
        )


# ─── parsing helpers ────────────────────────────────────────────────

def _strip_slash_hint(text: str) -> str:
    """Drop pm-agent's slash-command instruction line(s).

    pm-agent appends a guidance line like
    "→ Dùng /add <thông tin> để cung cấp thêm, hoặc /cancel để hủy yêu cầu."
    to need_more_info / auth prompts. Our chat UI replaces that with a reply
    input + Gửi/Hủy buttons, so we remove the line for display. Other content
    (e.g. an auth URL) is kept.
    """
    if not text:
        return text
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        is_slash_hint = ("/add" in stripped and "/cancel" in stripped)
        if is_slash_hint:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _parts_text(parts: Optional[list[dict]]) -> str:
    if not parts:
        return ""
    chunks = [p.get("text", "") for p in parts if p.get("kind") == "text"]
    return "\n".join(c for c in chunks if c).strip()


def _artifacts_text(artifacts: list[dict]) -> str:
    chunks = [_parts_text(a.get("parts")) for a in artifacts]
    return "\n".join(c for c in chunks if c).strip()


def _status_text(status: dict) -> str:
    message = status.get("message") or {}
    return _parts_text(message.get("parts"))


def _detect_approval(artifacts: list[dict]) -> tuple[bool, Optional[list[dict]]]:
    """An approval request is a DataPart whose data is a need_approval payload.

    pm-agent emits {"kind":"need_approval","message":..,"issues":[..],..} in a
    DataPart of an artifact named "approval_request". We detect either signal.
    """
    for artifact in artifacts:
        named_approval = artifact.get("name") == "approval_request"
        for part in artifact.get("parts") or []:
            if part.get("kind") != "data":
                continue
            data = part.get("data") or {}
            if data.get("kind") == "need_approval" or named_approval:
                issues = data.get("issues")
                return True, issues if isinstance(issues, list) else None
    return False, None


# ─── module-level accessor (per-service config; no shared client) ────

_singleton: Optional[PmAgentClient] = None


def get_pm_agent_client() -> PmAgentClient:
    """Lazy singleton built from env (PM_AGENT_URL / TOKEN_AUTHEN_PM_AGENT)."""
    global _singleton
    if _singleton is None:
        _singleton = PmAgentClient(
            url=os.getenv("PM_AGENT_URL", ""),
            api_key=os.getenv("TOKEN_AUTHEN_PM_AGENT", ""),
        )
    return _singleton
