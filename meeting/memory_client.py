"""
Lightweight AgentBase Memory client for Mee Agent.
Saves meeting events (transcript + notes summary) to AgentBase Memory Service.
Uses stdlib only — no extra dependencies.
"""
import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_token_cache: dict = {"token": None, "expires_at": 0}


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    client_id = os.getenv("GREENNODE_CLIENT_ID", "")
    client_secret = os.getenv("GREENNODE_CLIENT_SECRET", "")

    # Fallback to .greennode.json for local dev
    if not client_id or not client_secret:
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".greennode.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                cfg = json.load(f)
            client_id = cfg.get("client_id", "")
            client_secret = cfg.get("client_secret", "")

    if not client_id or not client_secret:
        raise RuntimeError("Missing GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET")

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token",
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())

    token = result["access_token"]
    try:
        payload_b64 = token.split(".")[1]
        pad = (4 - len(payload_b64) % 4) % 4
        payload = json.loads(base64.b64decode(payload_b64 + "=" * pad))
        _token_cache["expires_at"] = payload.get("exp", now + 3600)
    except Exception:
        _token_cache["expires_at"] = now + 3600
    _token_cache["token"] = token
    return token


def _post_event(memory_id: str, actor_id: str, session_id: str,
                role: str, message: str, token: str) -> None:
    url = (
        f"https://agentbase.api.vngcloud.vn/memory/memories"
        f"/{memory_id}/actors/{actor_id}/sessions/{session_id}/events"
    )
    payload = {"payload": {"type": "conversational", "role": role, "message": message}}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def save_meeting_events(session_id: str, notes: dict, transcript: str) -> None:
    """
    Save meeting transcript + notes summary to AgentBase Memory.
    Called in a background thread — failures are logged but never propagate.
    """
    memory_id = os.getenv("MEMORY_ID", "")
    if not memory_id:
        return

    try:
        token = _get_token()
    except Exception as e:
        logger.warning(f"Memory: cannot get token: {e}")
        return

    actor_id = "mee-user"
    title = notes.get("title", "Cuộc họp")
    date = notes.get("date", "")

    # User event: digest of transcript
    transcript_digest = f"[{date}] {title}: {transcript[:800]}"
    try:
        _post_event(memory_id, actor_id, session_id, "user", transcript_digest, token)
    except Exception as e:
        logger.warning(f"Memory: failed to save user event: {e}")

    # Assistant event: notes summary + top action items
    summary = notes.get("summary", "")
    actions = notes.get("action_items", [])
    actions_str = "; ".join(
        f"{a.get('pic', '')}: {a.get('item', '')}" for a in actions[:5]
    )
    notes_summary = f"Biên bản [{date}] {title}. {summary}"
    if actions_str:
        notes_summary += f" | Actions: {actions_str}"
    try:
        _post_event(memory_id, actor_id, session_id, "assistant", notes_summary, token)
        logger.info(f"Memory: events saved for session {session_id}")
    except Exception as e:
        logger.warning(f"Memory: failed to save assistant event: {e}")
