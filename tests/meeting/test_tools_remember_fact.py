"""remember_fact — HITL side-effect tool that stores a durable, recallable fact
into AgentBase, scoped by the actor-granularity decision:

  scope="user"    → user_prefs/<ms_oid>        (per-user: "gọi tôi là Ronaldo")
  scope="project" → project_facts/<meeting_id> (shared project fact)

Unit tests inject the ms_oid lookup (repo) and the AgentBase write (memory_client)
— no DB, no network.

Spec: docs/superpowers/specs/2026-06-16-chat-knowledge-capture-design.md
"""
from __future__ import annotations

import uuid

import src.services.tools.remember_fact as rf
from src.services import tools

MID = "11111111-1111-1111-1111-111111111111"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
OID = "entra-oid-ronaldo"


def _executor():
    return tools.get_tool("remember_fact")["executor"]


def _patch_oid(monkeypatch, oid):
    async def fake(session, user_id):
        return oid
    monkeypatch.setattr(rf.repo, "get_user_ms_oid", fake)


def _capture_insert(monkeypatch):
    sent = {}

    def fake_insert(text, *, namespace, scope, author_oid="", session_id="", **kw):
        sent.update(text=text, namespace=namespace, scope=scope, author_oid=author_oid)
        return {"ok": True}

    monkeypatch.setattr(rf.mc, "insert_fact_record", fake_insert)
    # No pre-existing facts (dedup browse returns nothing) + run the normally
    # fire-and-forget write inline so assertions are deterministic.
    monkeypatch.setattr(rf.mc, "list_fact_records", lambda *a, **k: [])
    monkeypatch.setattr(rf, "_dispatch_write", lambda writer: writer())
    return sent


def test_remember_fact_runs_without_approval():
    # Auto-capture: no HITL popup — it executes inline and writes in the background.
    spec = tools.get_tool("remember_fact")
    assert spec is not None
    assert spec["side_effect"] is False


def test_remember_fact_default_scope_is_project():
    schema = tools.get_tool("remember_fact")["schema"]
    assert schema["properties"]["scope"].get("default") == "project"


async def test_remember_fact_user_scope_writes_to_user_prefs(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)
    sent = _capture_insert(monkeypatch)

    out = await _executor()(
        {"text": "Gọi user là Ronaldo.", "scope": "user"},
        session=object(), user_id=UID,
    )

    assert out["status"] == "remembered"
    assert out["scope"] == "user"
    assert sent["namespace"] == f"user_prefs/{OID}"
    assert sent["text"] == "Gọi user là Ronaldo."
    assert sent["author_oid"] == OID


async def test_remember_fact_project_scope_writes_to_meeting_partition(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)
    sent = _capture_insert(monkeypatch)

    out = await _executor()(
        {"text": "Deadline dời sang 30/06.", "scope": "project", "meeting_id": MID},
        session=object(), user_id=UID,
    )

    assert out["status"] == "remembered"
    assert out["scope"] == "project"
    assert sent["namespace"] == f"project_facts/{MID}"
    # author still recorded for audit even on a shared project fact
    assert sent["author_oid"] == OID


async def test_remember_fact_project_scope_resolves_meeting_title(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)
    sent = _capture_insert(monkeypatch)

    class _Meeting:
        title = "AI Innovation Project"

    async def fake_get_meeting(session, mid):
        return _Meeting()
    monkeypatch.setattr(rf.repo, "get_meeting", fake_get_meeting)

    out = await _executor()(
        {"text": "Deadline dời sang 30/06.", "scope": "project", "meeting_id": MID},
        session=object(), user_id=UID,
    )

    # the project's natural-language name is surfaced (return + stored text),
    # while the namespace stays keyed by the stable meeting_id
    assert out["project_title"] == "AI Innovation Project"
    assert "AI Innovation Project" in sent["text"]
    assert sent["namespace"] == f"project_facts/{MID}"


async def test_remember_fact_project_scope_ok_when_meeting_title_missing(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)
    sent = _capture_insert(monkeypatch)

    async def fake_get_meeting(session, mid):
        return None  # title enrichment is best-effort; storing must still succeed
    monkeypatch.setattr(rf.repo, "get_meeting", fake_get_meeting)

    out = await _executor()(
        {"text": "Một fact dự án.", "scope": "project", "meeting_id": MID},
        session=object(), user_id=UID,
    )
    assert out["status"] == "remembered"
    assert sent["text"] == "Một fact dự án."


async def test_remember_fact_user_scope_errors_without_ms_oid(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, None)

    def boom(*a, **k):
        raise AssertionError("must not write without an ms_oid")
    monkeypatch.setattr(rf.mc, "insert_fact_record", boom)

    out = await _executor()(
        {"text": "Gọi user là Ronaldo.", "scope": "user"},
        session=object(), user_id=UID,
    )
    assert out.get("error")


async def test_remember_fact_project_scope_errors_without_meeting(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)

    def boom(*a, **k):
        raise AssertionError("must not write without a meeting_id")
    monkeypatch.setattr(rf.mc, "insert_fact_record", boom)

    out = await _executor()(
        {"text": "Một fact dự án.", "scope": "project"},
        session=object(), user_id=UID,
    )
    assert out.get("error")


async def test_remember_fact_requires_text(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    out = await _executor()({"text": "   ", "scope": "user"}, session=object(), user_id=UID)
    assert out.get("error")


async def test_remember_fact_skips_duplicate(monkeypatch):
    """Pollution guard: an identical fact already in the namespace isn't re-inserted
    (matters now that deduced facts persist with no approval gate)."""
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)
    monkeypatch.setattr(rf, "_dispatch_write", lambda writer: writer())  # inline
    monkeypatch.setattr(rf.mc, "list_fact_records", lambda *a, **k: ["Gọi user là Ronaldo."])
    calls: list[str] = []
    monkeypatch.setattr(rf.mc, "insert_fact_record", lambda *a, **k: calls.append("x"))

    out = await _executor()(
        {"text": "Gọi user là Ronaldo.", "scope": "user"}, session=object(), user_id=UID
    )
    assert out["status"] == "remembered"
    assert calls == []   # duplicate → no insert


async def test_remember_fact_runs_write_in_background(monkeypatch):
    """UX: the turn returns 'remembered' immediately; the AgentBase write is
    dispatched fire-and-forget, not awaited on the critical path."""
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)

    wrote: list[str] = []
    monkeypatch.setattr(rf.mc, "insert_fact_record", lambda *a, **k: wrote.append("x"))
    monkeypatch.setattr(rf.mc, "list_fact_records", lambda *a, **k: [])
    dispatched: list = []
    monkeypatch.setattr(rf, "_dispatch_write", lambda writer: dispatched.append(writer))

    out = await _executor()(
        {"text": "Gọi user là Ronaldo.", "scope": "user"}, session=object(), user_id=UID
    )

    assert out["status"] == "remembered"   # returned without waiting for the write
    assert wrote == []                      # write deferred (not run inline)
    assert len(dispatched) == 1             # it was scheduled in the background
    dispatched[0]()                         # when the bg task runs…
    assert wrote == ["x"]                   # …it performs the insert


def test_forget_fact_runs_without_approval():
    spec = tools.get_tool("forget_fact")
    assert spec is not None
    assert spec["side_effect"] is False


async def test_forget_fact_writes_inactive_tombstone(monkeypatch):
    """forget_fact hides a fact via a newer active=0 record keyed by its text —
    no delete (AgentBase DELETE is 403)."""
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    _patch_oid(monkeypatch, OID)
    monkeypatch.setattr(rf, "_dispatch_write", lambda writer: writer())

    sent = {}

    def fake_insert(text, *, namespace, scope, active=True, key=None, **kw):
        sent.update(text=text, namespace=namespace, active=active, key=key)
        return {"ok": True}
    monkeypatch.setattr(rf.mc, "insert_fact_record", fake_insert)

    out = await tools.get_tool("forget_fact")["executor"](
        {"text": "Gọi user là Ronaldo.", "scope": "user"}, session=object(), user_id=UID
    )

    assert out["status"] == "forgotten"
    assert sent["active"] is False
    assert sent["namespace"] == f"user_prefs/{OID}"
    assert sent["key"] == rf.mc.fact_key("Gọi user là Ronaldo.")


async def test_forget_fact_requires_text(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    out = await tools.get_tool("forget_fact")["executor"](
        {"text": "  ", "scope": "user"}, session=object(), user_id=UID
    )
    assert out.get("error")


async def test_remember_fact_disabled_without_memory_id(monkeypatch):
    monkeypatch.delenv("MEMORY_ID", raising=False)

    def boom(*a, **k):
        raise AssertionError("no network when AgentBase memory is unconfigured")
    monkeypatch.setattr(rf.mc, "insert_fact_record", boom)

    out = await _executor()(
        {"text": "Gọi user là Ronaldo.", "scope": "user"},
        session=object(), user_id=UID,
    )
    assert out["status"] == "disabled"
