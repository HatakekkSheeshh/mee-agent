"""Probe the AgentBase Memory WRITE/DELETE side for long-term memory-records.

Read side is verified (scripts/probe_memory_read.py). This probes the pieces the
Postgres→AgentBase sync writer depends on, which the docs DON'T fully specify:

  1. INSERT  — POST memory-records:insertDirectly  (docs: body {"records": [str]})
  2. ROUND-TRIP — does browse/search return the inserted string VERBATIM? (we need
     to embed a `source_hash` marker line inside the text, since insertDirectly
     has no metadata field).
  3. DELETE  — endpoint shape is UNDOCUMENTED; try several candidates so we know
     how to do delete-then-insert upsert.

Safe: inserts ONE uniquely-marked throwaway record, then attempts to delete it.
If every delete candidate fails, it prints the leftover record id so it can be
removed manually. Targets namespace `project_facts`.

Docs: https://docs.vngcloud.vn/vng-cloud-document/vn/ai-stack/agent-base/memory.md
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

# A unique, obviously-synthetic marker so we can find + clean up this probe row.
PROBE_MARKER = "PROBE_WRITE_DELETE_zzx91"
PROBE_TEXT = f"[{PROBE_MARKER}] project:00000000 hash=deadbeef — throwaway probe record, safe to delete."


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
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def call(token: str, method: str, url: str, body: dict | None = None) -> tuple[int, object]:
    """Return (status_code, parsed_body_or_text). status 0 == transport error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _records_of(browse_result: object) -> list[dict]:
    """AgentBase browse responses vary in envelope; pull the list of record dicts."""
    if isinstance(browse_result, dict):
        for key in ("listData", "data", "records", "items"):
            if isinstance(browse_result.get(key), list):
                return browse_result[key]
    if isinstance(browse_result, list):
        return browse_result
    return []


def main() -> int:
    token = get_token()
    print("✓ Auth token acquired.\n")

    memory_id = os.getenv("MEMORY_ID", "").strip() or "memory-34e0820d-bf0c-47fa-9f37-2f18b5112329"
    actor_id = (os.getenv("MEMORY_ACTOR_ID") or "mee-user").strip()
    print(f"memory_id = {memory_id}")
    print(f"actor_id  = {actor_id}\n")

    # 1. Resolve the project_facts namespace from strategy templates, SUBSTITUTING
    #    the {actorId} placeholder — the prior probe passed the literal template,
    #    which is an invalid namespace. We must rule that out as the 403 cause.
    status, strategies = call(token, "GET", f"{BASE}/memories/{memory_id}/long-term-memory-strategies")
    templates = [s.get("namespaceTemplate") for s in (strategies or []) if isinstance(s, dict)]
    print(f"namespaceTemplates seen: {templates}")
    raw_ns = next((t for t in templates if t and "project_facts" in t), None) or "project_facts/{actorId}"
    ns = raw_ns.replace("{actorId}", actor_id).replace("{actor_id}", actor_id)
    print(f"→ template {raw_ns!r}  →  resolved namespace {ns!r}\n")
    # The canonical client (memory.sh build_query) passes the namespace RAW —
    # no %2F encoding. Mirror that exactly.
    ns_enc = ns

    # CONTROL: read the SAME resolved namespace. If this returns 200 but insert
    # 403s, the denial is action-level (write permission), not namespace/route.
    st_ctrl, _ = call(token, "GET", f"{BASE}/memories/{memory_id}/memory-records?namespace={ns_enc}&limit=5")
    print(f"── [0] READ control on resolved namespace → [{st_ctrl}] (200 here + 403 on insert ⇒ write perm missing)\n")

    # 2. INSERT one throwaway record.
    #    Canonical route is `:insert-directly` (hyphen) with body {"memoryRecords":[...]},
    #    per memory.sh do_records_insert — the docs' `:insertDirectly`/`{"records":...}`
    #    was wrong and produced unknown-route 403s.
    print("── [1] INSERT (memory-records:insert-directly) ──")
    ins_url = f"{BASE}/memories/{memory_id}/memory-records:insert-directly?namespace={ns_enc}"
    st, resp = call(token, "POST", ins_url, body={"memoryRecords": [PROBE_TEXT]})
    print(f"  POST insertDirectly → [{st}] {json.dumps(resp, ensure_ascii=False)[:400] if isinstance(resp, (dict, list)) else resp}\n")

    # 3. BROWSE → find our marker, capture the record id, check verbatim round-trip.
    print("── [2] BROWSE → round-trip check ──")
    st, browse = call(token, "GET", f"{BASE}/memories/{memory_id}/memory-records?namespace={ns_enc}&limit=200")
    recs = _records_of(browse)
    print(f"  GET browse → [{st}] {len(recs)} record(s) in namespace")
    mine = [r for r in recs if PROBE_MARKER in json.dumps(r, ensure_ascii=False)]
    if mine:
        rec = mine[0]
        print(f"  ✓ found our probe record. Full shape:\n{json.dumps(rec, indent=2, ensure_ascii=False)[:900]}")
        verbatim = PROBE_TEXT in json.dumps(rec, ensure_ascii=False)
        print(f"  → verbatim round-trip (marker text preserved): {verbatim}")
    else:
        rec = None
        print("  ✗ probe record NOT found on browse (may be async-indexed; re-run browse, or check search).")

    # capture an id field, whatever it's called
    rec_id = None
    if rec:
        for key in ("id", "recordId", "memoryRecordId", "factId", "uid"):
            if rec.get(key):
                rec_id = rec[key]
                print(f"  → record id field: {key} = {rec_id}")
                break

    # 4. DELETE — try candidate shapes (undocumented). Stop at first success.
    print("\n── [3] DELETE (trying undocumented candidates) ──")
    if not rec_id:
        print("  (no record id captured — delete probes will likely 404; running anyway for status shapes)")
    rid = rec_id or "REPLACE_WITH_ID"
    # Canonical (memory.sh do_records_delete): DELETE .../memory-records/{id}, no namespace.
    candidates = [
        ("DELETE", f"{BASE}/memories/{memory_id}/memory-records/{rid}", None),
        ("DELETE", f"{BASE}/memories/{memory_id}/memory-records/{rid}?namespace={ns_enc}", None),
    ]
    deleted = False
    for method, url, body in candidates:
        st, resp = call(token, method, url, body=body)
        short = json.dumps(resp, ensure_ascii=False)[:200] if isinstance(resp, (dict, list)) else str(resp)[:200]
        ok = st in (200, 202, 204)
        print(f"  [{st}] {method} {url.replace(BASE, '')}  body={body}\n        → {short}")
        if ok:
            print(f"  ✓ DELETE SUCCEEDED with: {method} {url.replace(BASE, '')} body={body}")
            deleted = True
            break

    # 5. Confirm / report leftover.
    print("\n── [4] Verify cleanup ──")
    st, browse2 = call(token, "GET", f"{BASE}/memories/{memory_id}/memory-records?namespace={ns_enc}&limit=200")
    still = [r for r in _records_of(browse2) if PROBE_MARKER in json.dumps(r, ensure_ascii=False)]
    if still:
        print(f"  ⚠ probe record STILL present (id={rec_id}). No working delete shape found — remove manually.")
    else:
        print("  ✓ probe record gone.")

    print("\n=== SUMMARY (paste this back) ===")
    print(f"  insert_ok      : {'unknown' if not mine and not rec else bool(mine or rec)}")
    print(f"  verbatim_text  : {PROBE_TEXT in json.dumps(mine[0], ensure_ascii=False) if mine else 'unknown'}")
    print(f"  record_id_field: {rec_id if rec_id else 'NONE FOUND'}")
    print(f"  delete_worked  : {deleted}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")
        sys.exit(1)
