"""
Lightweight AgentBase Memory client for Mee Agent.
Saves meeting events (transcript + notes summary) to AgentBase Memory Service.
Uses stdlib only — no extra dependencies.

Also hosts the project-state memory-records layer used by the Postgres→AgentBase
sync (scripts/sync_memory.py). AgentBase memory-records have NO metadata field and
record DELETE is denied for our service account (confirmed via probe), so v1 is
**insert-only, newest-wins**: each project's distilled state is written as one
record whose first line is a machine-readable marker
`[mee-sync project=<id> hash=<source_hash>]`. Change detection reads back the
latest record for a project and compares the embedded hash. Pure helpers
(build/parse/select) are unit-tested without network; the network functions take
an injectable `call` seam.

Confirmed write contract (memory `memory-0a6ff6dc-…`, namespace `project_facts/<actor>`):
  POST /memory/memories/{id}/memory-records:insert-directly?namespace=<ns>
       body {"memoryRecords": ["<text>"]}        (raw namespace, no %2F)
"""
import base64
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# ── Project-state memory-records (sync projection) ──────────────────────────
PROJECT_FACTS_PREFIX = "project_facts"   # strategy namespaceTemplate is "project_facts/{actorId}"
DEFAULT_ACTOR_ID = "mee-user"
SYNC_MARKER = "mee-sync"
_MEMORY_BASE = os.getenv("AGENTBASE_MEMORY_URL", "https://agentbase.api.vngcloud.vn/memory")
_MARKER_RE = re.compile(rf"^\[{SYNC_MARKER} project=(?P<pid>\S+) hash=(?P<hash>\S+)\]")

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


# ── Pure helpers (network-free, unit-tested) ────────────────────────────────

def build_project_record_text(
    project_id: str, source_hash: str, state_text: str, *, title: str | None = None
) -> str:
    """Embed the change-detection marker as the record's first line.

    AgentBase records have no metadata field, so the source_hash rides inside
    the text. `parse_project_marker` is the inverse. An optional `title` is added
    as a deterministic header after the marker — the LLM echoes the project name
    inconsistently, and a stable title materially improves semantic recall.
    """
    marker = f"[{SYNC_MARKER} project={project_id} hash={source_hash}]"
    body = f"# {title}\n\n{state_text}" if title else state_text
    return f"{marker}\n{body}"


def parse_project_marker(memory_text: str | None) -> dict | None:
    """Extract {'project_id', 'hash'} from a record's marker line, or None."""
    m = _MARKER_RE.match((memory_text or "").lstrip())
    if not m:
        return None
    return {"project_id": m.group("pid"), "hash": m.group("hash")}


# Appended to the recalled body when load_context detects the distillation is
# stale vs current Postgres data (Q1 staleness check). Honest-now, non-blocking:
# the agent is told to read real data via list_recordings/recording_mom rather
# than trust a distillation that may predate the newest session.
STALE_NOTE = (
    "⚠ Lưu ý: bản chắt lọc trên có thể CHƯA gồm phiên/cập nhật mới nhất. "
    "Nếu user hỏi về một phiên cụ thể hay số liệu mới, hãy dùng `list_recordings`/"
    "`recording_mom` để đọc dữ liệu thật trước khi trả lời, đừng chỉ dựa vào bản chắt lọc."
)


def is_record_stale(memory_text: str | None, live_hash: str) -> bool:
    """True if the record's embedded marker hash differs from `live_hash`.

    `live_hash` = canonical_source_hash of the project's CURRENT Postgres data.
    A markerless/None record returns False: freshness can't be proven, so we don't
    raise a false alarm — only a genuine hash disagreement signals a distillation
    that predates new/changed sessions.
    """
    marker = parse_project_marker(memory_text)
    if not marker:
        return False
    return marker["hash"] != live_hash


def strip_project_marker(memory_text: str | None) -> str:
    """Human-readable body of a project record — the marker line removed.

    The chat agent recalls this (title header + distilled state); the
    `[mee-sync project=… hash=…]` line is internal bookkeeping it shouldn't see.
    """
    text = (memory_text or "").lstrip()
    if _MARKER_RE.match(text):
        parts = text.split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    return text.strip()


def _records_of(resp: object) -> list:
    """Normalize AgentBase browse/search envelopes to a list of record dicts."""
    if isinstance(resp, dict):
        for key in ("listData", "data", "records", "items"):
            val = resp.get(key)
            if isinstance(val, list):
                return val
    if isinstance(resp, list):
        return resp
    return []


def select_latest_project_record(records: list, project_id: str) -> dict | None:
    """Newest record (max created_at) whose marker matches project_id, or None.

    Implements the insert-only "newest-wins" read: older state records for the
    same project linger (DELETE is denied) but the latest one is authoritative.
    """
    matches = []
    for rec in records or []:
        marker = parse_project_marker(rec.get("memory") if isinstance(rec, dict) else None)
        if marker and marker["project_id"] == str(project_id):
            matches.append(rec)
    if not matches:
        return None
    return max(matches, key=lambda r: r.get("created_at") or "")


# ── Network functions (injectable `call` seam) ──────────────────────────────

def _default_call(method: str, url: str, body: dict | None, token: str) -> object:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else None


def _namespace(actor_id: str) -> str:
    return f"{PROJECT_FACTS_PREFIX}/{actor_id}"


# ── User persona (role) read — namespace user_prefs/{actorId} ───────────────
USER_PREFS_PREFIX = "user_prefs"
_ROLE_LINE_RE = re.compile(r"(?:role|vai\s*tr[òo])\s*[:=]\s*(?P<role>.+)", re.IGNORECASE)


def _user_prefs_namespace(actor_id: str) -> str:
    return f"{USER_PREFS_PREFIX}/{actor_id}"


def parse_user_role(records: list) -> str | None:
    """Extract the role from the newest `user_prefs` record carrying one, or None.

    Newest-wins, but role-aware: scans records newest-first and returns the role
    from the first one that HAS a `role:` / `vai trò:` line. A newer record
    without a role line (e.g. a remembered `[mee-fact]` written to the same
    `user_prefs/{actor}` namespace) is skipped, not treated as "role cleared".
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    for rec in sorted(recs, key=lambda r: r.get("created_at") or "", reverse=True):
        text = rec.get("memory") or ""
        for line in text.splitlines():
            m = _ROLE_LINE_RE.search(line)
            if m:
                role = m.group("role").strip().strip('".')
                if role:
                    return role
    return None


def get_user_role(
    actor_id: str = DEFAULT_ACTOR_ID,
    *,
    memory_id: str | None = None,
    token: str | None = None,
    call=_default_call,
) -> str | None:
    """The user's role from AgentBase `user_prefs/{actorId}`, or None.

    Best-effort, mirrors `search_project_record`: never raises — returns None on
    missing config, a miss, or any network/parse error, so kickoff never blocks.
    """
    try:
        memory_id = memory_id or os.getenv("MEMORY_ID", "")
        if not memory_id:
            return None
        token = token or _get_token()
        ns = _user_prefs_namespace(actor_id)
        url = (
            f"{_MEMORY_BASE}/memories/{memory_id}/memory-records"
            f"?namespace={ns}&limit=50"
        )
        resp = call("GET", url, None, token)
        return parse_user_role(_records_of(resp))
    except Exception as e:  # best-effort: never block chat open
        logger.warning("get_user_role failed: %s", e)
        return None


def search_project_record(
    project_id: str,
    *,
    memory_id: str | None = None,
    actor_id: str = DEFAULT_ACTOR_ID,
    token: str | None = None,
    call=_default_call,
) -> dict | None:
    """Fetch the latest project-state record for `project_id`, or None.

    Browses the project_facts namespace and picks the newest marker match.
    `call`/`token`/`memory_id` are injectable so this is unit-testable offline.
    """
    memory_id = memory_id or os.getenv("MEMORY_ID", "")
    if not memory_id:
        return None
    token = token or _get_token()
    ns = _namespace(actor_id)
    url = (
        f"{_MEMORY_BASE}/memories/{memory_id}/memory-records"
        f"?namespace={ns}&limit=200"
    )
    resp = call("GET", url, None, token)
    return select_latest_project_record(_records_of(resp), project_id)


def upsert_project_record(
    project_id: str,
    text: str,
    source_hash: str,
    *,
    title: str | None = None,
    memory_id: str | None = None,
    actor_id: str = DEFAULT_ACTOR_ID,
    token: str | None = None,
    call=_default_call,
) -> object:
    """Insert one project-state record (insert-only; DELETE is denied for our SA).

    The record text carries the marker line so the next sync can compare hashes,
    and a deterministic `# {title}` header for recall.
    """
    memory_id = memory_id or os.getenv("MEMORY_ID", "")
    if not memory_id:
        raise RuntimeError("MEMORY_ID not set")
    token = token or _get_token()
    ns = _namespace(actor_id)
    url = (
        f"{_MEMORY_BASE}/memories/{memory_id}/memory-records:insert-directly"
        f"?namespace={ns}"
    )
    record_text = build_project_record_text(project_id, source_hash, text, title=title)
    return call("POST", url, {"memoryRecords": [record_text]}, token)


# ── Chat-captured fact records (remember_fact) ───────────────────────────────
# A durable fact the chat agent stored (user-asserted or agent-deduced, always
# HITL-approved). Distinct from the `[mee-sync …]` distillation blob: it carries
# a `[mee-fact scope=… author=… session=…]` marker (audit + disambiguation) and
# lives in a scope-specific namespace so read==write (the actor-granularity
# decision): user facts → `user_prefs/<ms_oid>`, project facts →
# `project_facts/<meeting_id>`. Insert-only, newest-wins (DELETE is denied).
FACT_MARKER = "mee-fact"
_FACT_MARKER_RE = re.compile(rf"^\[{FACT_MARKER}\s+(?P<fields>[^\]]*)\]")


def fact_key(text: str) -> str:
    """Stable short key identifying a logical fact by its NORMALIZED text.

    Same key ⇒ same fact, so a later `active=0` record (forget_fact) supersedes an
    earlier `active=1` one (and vice-versa) under newest-wins. Normalization is
    lowercase + whitespace-collapse — enough to match re-assertions of "the same"
    fact without over-merging distinct ones.
    """
    norm = " ".join((text or "").lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def build_fact_record_text(
    text: str,
    *,
    scope: str,
    active: bool = True,
    author_oid: str = "",
    session_id: str = "",
    key: str | None = None,
) -> str:
    """Embed the audit/control marker as the record's first line; `text` is the body.

    Marker carries `key` (logical-fact identity for newest-wins supersede),
    `active` (0 = a forget tombstone hides this fact), plus `author`/`session` for
    audit. `key` defaults to `fact_key(text)` — callers pass it explicitly when the
    display body differs from the raw text used to key the fact (e.g. a project
    fact decorated with "(Dự án …)").
    """
    k = key or fact_key(text)
    a = "1" if active else "0"
    marker = (
        f"[{FACT_MARKER} scope={scope} key={k} active={a} "
        f"author={author_oid or '-'} session={session_id or '-'}]"
    )
    return f"{marker}\n{(text or '').strip()}"


def _grab_field(fields: str, name: str) -> str | None:
    m = re.search(rf"{name}=(\S+)", fields)
    return m.group(1) if m else None


def parse_fact_marker(memory_text: str | None) -> dict | None:
    """Extract {'scope','key','active','author','session'} from a fact marker, or None.

    Tolerant of field order and of legacy markers without key/active (active
    defaults True, key None). Returns None for unmarked text and `[mee-sync …]`
    distillation blobs so the record kinds never get confused in one namespace.
    """
    m = _FACT_MARKER_RE.match((memory_text or "").lstrip())
    if not m:
        return None
    fields = m.group("fields")
    scope = _grab_field(fields, "scope")
    if scope is None:
        return None
    active_raw = _grab_field(fields, "active")
    active = True if active_raw is None else active_raw not in ("0", "false", "False")
    return {
        "scope": scope,
        "key": _grab_field(fields, "key"),
        "active": active,
        "author": _grab_field(fields, "author"),
        "session": _grab_field(fields, "session"),
    }


def strip_fact_marker(memory_text: str | None) -> str:
    """Human-readable body of a fact record — the marker line removed."""
    text = (memory_text or "").lstrip()
    if _FACT_MARKER_RE.match(text):
        parts = text.split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    return text.strip()


def fact_namespace(scope: str, actor_id: str) -> str:
    """AgentBase namespace for a fact of `scope` keyed by `actor_id`.

    user → `user_prefs/<ms_oid>` (per-user); project → `project_facts/<meeting_id>`
    (shared across the project's users, partitioned by meeting — never ms_oid,
    which would silo project knowledge per user).
    """
    if scope == "user":
        return f"{USER_PREFS_PREFIX}/{actor_id}"
    if scope == "project":
        return f"{PROJECT_FACTS_PREFIX}/{actor_id}"
    raise ValueError(f"unknown fact scope: {scope!r}")


def insert_fact_record(
    text: str,
    *,
    namespace: str,
    scope: str,
    active: bool = True,
    key: str | None = None,
    author_oid: str = "",
    session_id: str = "",
    memory_id: str | None = None,
    token: str | None = None,
    call=_default_call,
) -> object:
    """Insert ONE fact record into `namespace` (insert-only; DELETE is denied).

    `active=False` writes a forget tombstone that hides the fact with this `key`.
    """
    memory_id = memory_id or os.getenv("MEMORY_ID", "")
    if not memory_id:
        raise RuntimeError("MEMORY_ID not set")
    token = token or _get_token()
    url = (
        f"{_MEMORY_BASE}/memories/{memory_id}/memory-records:insert-directly"
        f"?namespace={namespace}"
    )
    record_text = build_fact_record_text(
        text, scope=scope, active=active, author_oid=author_oid,
        session_id=session_id, key=key,
    )
    return call("POST", url, {"memoryRecords": [record_text]}, token)


def list_fact_records(
    namespace: str,
    *,
    memory_id: str | None = None,
    token: str | None = None,
    call=_default_call,
) -> list[str]:
    """Browse `namespace` → ACTIVE fact bodies (marker-stripped), newest-first.

    Newest-wins per `key`: for each logical fact only the latest record counts, and
    if that latest record is a forget tombstone (`active=0`) the fact is hidden.
    Re-asserting later (a newer `active=1`) brings it back. Only `[mee-fact …]`
    records are considered; distillation blobs are ignored. [] when MEMORY_ID unset.
    """
    memory_id = memory_id or os.getenv("MEMORY_ID", "")
    if not memory_id:
        return []
    token = token or _get_token()
    url = (
        f"{_MEMORY_BASE}/memories/{memory_id}/memory-records"
        f"?namespace={namespace}&limit=200"
    )
    resp = call("GET", url, None, token)
    parsed = []
    for r in _records_of(resp):
        if not isinstance(r, dict):
            continue
        marker = parse_fact_marker(r.get("memory"))
        if marker:
            parsed.append((r.get("created_at") or "", marker, r))
    parsed.sort(key=lambda t: t[0], reverse=True)  # newest first

    seen: set[str] = set()
    out: list[str] = []
    for _created, marker, r in parsed:
        body = strip_fact_marker(r.get("memory"))
        key = marker.get("key") or f"_nokey:{body}"  # legacy records key by body
        if key in seen:
            continue  # an older record for a fact already decided by its newest
        seen.add(key)
        if marker.get("active", True):
            out.append(body)
    return out
