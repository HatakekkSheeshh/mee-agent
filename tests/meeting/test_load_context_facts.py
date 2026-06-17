"""Read side of remember_fact: load_context recalls stored facts into the prompt.

  - user-scoped facts (user_prefs/<ms_oid>) surface even with NO meeting bound
    (e.g. "gọi tôi là Ronaldo" must be recalled in a general chat too);
  - project-scoped facts (project_facts/<meeting_id>) surface for the turn's meeting;
  - no facts → no "Ghi nhớ" block, project_memory unchanged.

Network-free: the AgentBase browse (list_facts) is injected via make_load_context.
"""
from __future__ import annotations

import uuid

from src.graphs.chat_graph import context as ctx

OID = "entra-oid-ronaldo"


class _User:
    def __init__(self, oid):
        self.ms_oid = oid
        self.display_name = "Anh"
        self.email = "annd2@vng.com.vn"
        self.role = None


class _Session:
    def __init__(self, user):
        self._user = user

    async def get(self, model, pk):
        return self._user


class _Meeting:
    def __init__(self):
        self.id = uuid.uuid4()
        self.title = "AI Innovation Project"
        self.project_summary_json = None
        self.recordings = []


def _patch_repo(monkeypatch, meeting=None):
    async def fake_list_chat_messages(session, sid, limit=10):
        return []

    async def fake_get_meeting(session, mid):
        return meeting

    async def fake_roster(session, uid):
        return []

    monkeypatch.setattr(ctx.repo, "list_chat_messages", fake_list_chat_messages)
    monkeypatch.setattr(ctx.repo, "get_meeting", fake_get_meeting)
    monkeypatch.setattr(ctx.repo, "list_meetings_for_user", fake_roster)


async def test_user_fact_recalled_without_a_meeting(monkeypatch):
    _patch_repo(monkeypatch)

    def fake_list_facts(namespace, **kw):
        return ["Gọi user là Ronaldo."] if namespace == f"user_prefs/{OID}" else []

    load_context = ctx.make_load_context(
        session=_Session(_User(OID)),
        list_facts=fake_list_facts,
    )
    out = await load_context({
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "meeting_id": None,
    })

    assert "Gọi user là Ronaldo." in out["project_memory"]


async def test_project_fact_recalled_for_the_turn_meeting(monkeypatch):
    meeting = _Meeting()
    _patch_repo(monkeypatch, meeting)

    def fake_list_facts(namespace, **kw):
        if namespace == f"project_facts/{meeting.id}":
            return ["Deadline dời sang 30/06."]
        return []

    load_context = ctx.make_load_context(
        session=_Session(_User(OID)),
        search_record=lambda pid: None,          # no distillation blob
        list_facts=fake_list_facts,
    )
    out = await load_context({
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "meeting_id": str(meeting.id),
    })

    assert "Deadline dời sang 30/06." in out["project_memory"]


async def test_recalled_facts_are_capped(monkeypatch):
    """Context guard: only the N newest facts are injected so the prompt doesn't
    bloat as memory grows. list_fact_records returns newest-first."""
    _patch_repo(monkeypatch)
    many = [f"fact {i}" for i in range(50)]

    load_context = ctx.make_load_context(
        session=_Session(_User(OID)),
        list_facts=lambda namespace, **kw: many,
    )
    out = await load_context({
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "meeting_id": None,
    })

    assert out["project_memory"].count("\n- ") == ctx.MAX_RECALLED_FACTS
    assert "fact 0" in out["project_memory"]                       # newest kept
    assert f"fact {ctx.MAX_RECALLED_FACTS}" not in out["project_memory"]  # overflow dropped


async def test_no_facts_no_remember_block(monkeypatch):
    _patch_repo(monkeypatch)

    load_context = ctx.make_load_context(
        session=_Session(_User(OID)),
        list_facts=lambda namespace, **kw: [],
    )
    out = await load_context({
        "session_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "meeting_id": None,
    })

    assert "Ghi nhớ" not in out["project_memory"]
    assert out["project_memory"] == ""
