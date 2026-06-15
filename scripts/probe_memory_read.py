"""Probe the AgentBase Memory API (read side) using the documented endpoints.

Docs: https://docs.vngcloud.vn/vng-cloud-document/vn/ai-stack/agent-base/memory.md
Requires: AgentBaseFullAccess policy on the service account.
"""
import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

import dotenv

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
dotenv.load_dotenv(PROJECT_ROOT / ".env", override=True, interpolate=False)
sys.path.append(str(PROJECT_ROOT))

BASE = "https://agentbase.api.vngcloud.vn/memory"
TOKEN_URL = "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"


def get_token() -> str:
    client_id = (os.getenv("GREENNODE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GREENNODE_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        json_path = PROJECT_ROOT / ".greennode.json"
        if json_path.exists():
            cfg = json.loads(json_path.read_text())
            client_id = cfg.get("client_id", "")
            client_secret = cfg.get("client_secret", "")

    if not client_id or not client_secret:
        raise RuntimeError("Missing GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET")

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def call(token: str, method: str, url: str, body: dict | None = None) -> dict | list | None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  FAILED [{e.code}] {method} {url}: {e.read().decode()[:300]}")
        return None


def main() -> int:
    token = get_token()
    print("Auth token acquired.")

    memory_id = os.getenv("MEMORY_ID", "").strip()
    actor_id = os.getenv("MEMORY_ACTOR_ID", "mee-user")

    # Resolve memory_id from the list endpoint if not configured
    memories = call(token, "GET", f"{BASE}/memories?page=1&size=10")
    if memories:
        print("\n=== Memories ===")
        for m in memories.get("listData", []):
            print(f"  {m['id']}  name={m['name']}  status={m['status']}")
            if not memory_id:
                memory_id = m["id"]
    if not memory_id:
        print("No memory found; set MEMORY_ID in .env")
        return 1

    # Long-term memory strategies (needed for record generation/namespaces)
    strategies = call(token, "GET", f"{BASE}/memories/{memory_id}/long-term-memory-strategies")
    print("\n=== Long-term strategies ===")
    print(json.dumps(strategies, indent=2, ensure_ascii=False)[:1500])

    # Actors known to this memory
    actors = call(token, "GET", f"{BASE}/memories/{memory_id}/actors?page=1&size=10")
    print("\n=== Actors ===")
    print(json.dumps(actors, indent=2, ensure_ascii=False)[:1500])

    # Namespaces are defined by each strategy's namespaceTemplate
    namespaces = [s["namespaceTemplate"] for s in (strategies or []) if s.get("namespaceTemplate")]
    for ns in namespaces:
        ns_enc = urllib.parse.quote(ns, safe="")

        records = call(
            token, "GET", f"{BASE}/memories/{memory_id}/memory-records?namespace={ns_enc}&limit=100"
        )
        print(f"\n=== Memory records (browse, namespace={ns}) ===")
        print(json.dumps(records, indent=2, ensure_ascii=False)[:2000])

        search = call(
            token,
            "POST",
            f"{BASE}/memories/{memory_id}/memory-records:search?namespace={ns_enc}",
            body={"query": "meeting", "limit": 10, "scoreThreshold": 0.0},
        )
        print(f"\n=== Memory records (search 'meeting', namespace={ns}) ===")
        print(json.dumps(search, indent=2, ensure_ascii=False)[:2000])

    # Short-term events require a session id; list sessions per actor if available
    sessions = call(
        token, "GET", f"{BASE}/memories/{memory_id}/actors/{actor_id}/sessions?page=1&size=10"
    )
    print(f"\n=== Sessions for actor '{actor_id}' ===")
    print(json.dumps(sessions, indent=2, ensure_ascii=False)[:1500])

    if sessions and sessions.get("listData"):
        session_id = sessions["listData"][0].get("id") or sessions["listData"][0].get("sessionId")
        events = call(
            token,
            "GET",
            f"{BASE}/memories/{memory_id}/actors/{actor_id}/sessions/{session_id}/events?page=1&size=20",
        )
        print(f"\n=== Events (session {session_id}) ===")
        print(json.dumps(events, indent=2, ensure_ascii=False)[:2000])

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
