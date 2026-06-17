"""The three parked follow-ups (HANDOFF 2026-06-10):

1. create_task assignee filter across the login↔display-name gap + recording scoping
2. reconcile per-assignee chunking (gateway timeout on big payloads)
3. `reason` (approval note) wired into the reconcile text
"""
import uuid

from src.graphs import chat_graph
from src.graphs.chat_graph import (
    MAX_RECONCILE_ITEMS,
    _reconcile_payloads,
    pm_reply,
    route_after_pm_reply,
)
from src.services.tools.create_task import assignee_matches

MID = "11111111-1111-1111-1111-111111111111"
RID = "33333333-3333-3333-3333-333333333333"
UID = "22222222-2222-2222-2222-222222222222"


# ─── 1a. assignee matching (login ↔ display name) ─────────────────

def test_assignee_matches_login_to_display_name():
    assert assignee_matches("hieunq3", "Hiếu")          # login contains bare name
    assert assignee_matches("Hiếu", "hieunq3")          # either direction
    assert assignee_matches("duy anh", "Duy Anh")       # case-insensitive (old behavior)
    assert assignee_matches("Dũng", "dungnt2")          # đ/ũ diacritics stripped


def test_assignee_matches_rejects_non_matches_and_empties():
    assert not assignee_matches("hieunq3", "Mai")
    assert not assignee_matches("", "Hiếu")
    assert not assignee_matches("Hiếu", "")


async def test_build_template_filters_by_login(monkeypatch):
    async def fake_items(session, mid):
        return [
            {"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"},
            {"pic": "Mai", "deadline": "11/01", "item": "POC caching"},
        ]

    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)
    tpl = await chat_graph._build_reconcile_template(
        object(), {"assignee": "hieunq3"}, {"title": "GIP"}, MID
    )
    assert len(tpl["items"]) == 1
    assert tpl["items"][0]["subject"] == "viết migration"


# ─── 1b. recording scoping ────────────────────────────────────────

async def test_build_template_scopes_to_recording(monkeypatch):
    async def fake_recording_mom(session, rid):
        assert rid == uuid.UUID(RID)
        return {"action_items": [{"pic": "Mai", "deadline": "01/07", "item": "deploy v1"}]}

    async def fail_project_wide(session, mid):  # must NOT be used when scoped
        raise AssertionError("project-wide aggregation called despite recording_id")

    monkeypatch.setattr(chat_graph.repo, "get_recording_mom", fake_recording_mom)
    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fail_project_wide)

    tpl = await chat_graph._build_reconcile_template(
        object(), {"recording_id": RID}, {"title": "GIP"}, MID
    )
    assert [it["subject"] for it in tpl["items"]] == ["deploy v1"]


async def test_build_template_invalid_recording_id_yields_empty(monkeypatch):
    tpl = await chat_graph._build_reconcile_template(
        object(), {"recording_id": "not-a-uuid"}, {"title": "GIP"}, None
    )
    assert tpl["items"] == []


# ─── 2. per-assignee chunked reconcile payloads ───────────────────

def test_payloads_one_per_assignee_group():
    items = [
        {"subject": "a", "assignee": "Hiếu"},
        {"subject": "b", "assignee": "Mai"},
        {"subject": "c", "assignee": "hiếu"},  # same group, case-insensitive
    ]
    payloads = _reconcile_payloads("GIP", items)
    assert len(payloads) == 2
    assert [it["subject"] for it in payloads[0]["items"]] == ["a", "c"]
    assert [it["subject"] for it in payloads[1]["items"]] == ["b"]
    assert all(p["kind"] == "reconcile" and p["project"] == "GIP" for p in payloads)


def test_payloads_subchunk_oversized_group():
    items = [{"subject": f"t{i}", "assignee": "Hiếu"} for i in range(23)]
    payloads = _reconcile_payloads("GIP", items)
    assert len(payloads) == 3  # 23 → 8 + 8 + 7
    assert all(len(p["items"]) <= MAX_RECONCILE_ITEMS for p in payloads)
    assert sum(len(p["items"]) for p in payloads) == 23


def test_payloads_small_template_stays_single_send():
    payloads = _reconcile_payloads("GIP", [{"subject": "x", "assignee": "Mai"}])
    assert len(payloads) == 1
    assert payloads[0]["text"] == chat_graph._reconcile_text(
        "GIP", payloads[0]["items"]
    )  # identical to the pre-chunking shape


def test_payloads_empty_items_still_one_payload():
    payloads = _reconcile_payloads("GIP", [])
    assert len(payloads) == 1 and payloads[0]["items"] == []


async def test_pm_reply_drains_queue_with_fresh_task():
    next_payload = {"kind": "reconcile", "project": "GIP", "items": [], "text": "g2"}
    out = await pm_reply({
        "pm_last": {"text": "nhóm 1 xong", "state": "completed", "task_id": "t1"},
        "pm_queue": [next_payload],
        "pm_replies": [],
        "pm_task_id": "t1",
        "pm_context_id": "c1",
    })
    assert out["pm_route"] == "next"
    assert out["pm_next_payload"] == next_payload
    assert out["pm_queue"] == []
    assert out["pm_replies"] == ["nhóm 1 xong"]
    assert out["pm_task_id"] is None and out["pm_context_id"] is None
    assert out["pm_rounds"] == 0


async def test_pm_reply_empty_queue_joins_accumulated_replies():
    out = await pm_reply({
        "pm_last": {"text": "nhóm 2 xong", "state": "completed", "task_id": "t2"},
        "pm_queue": [],
        "pm_replies": ["nhóm 1 xong"],
    })
    assert out["final_reply"] == "nhóm 1 xong\n\n---\n\nnhóm 2 xong"
    assert out["tool_result"]["via"] == "pm_agent"


def test_route_after_pm_reply():
    assert route_after_pm_reply({"pm_route": "next"}) == "pm_call"
    assert route_after_pm_reply({"pm_route": "reply"}) == "save_reply"
    assert route_after_pm_reply({}) == "save_reply"


# ─── 3. reason note threaded into the reconcile text ──────────────

def test_reconcile_text_appends_note():
    text = chat_graph._reconcile_text("GIP", [{"subject": "x"}], note="ưu tiên cao")
    assert text.endswith("Ghi chú của người duyệt: ưu tiên cao")
    assert "Ghi chú" not in chat_graph._reconcile_text("GIP", [{"subject": "x"}])


class _ApplyToolset:
    """Minimal injected toolset that records the MCP write calls."""

    def __init__(self):
        self.calls = []

    def list_tools(self):
        return []

    def get_tool(self, n):
        return None

    async def execute_tool(self, name, args, *, session, user_id):
        self.calls.append({"name": name, "args": args})
        return {"id": 100 + len(self.calls)}


async def test_agent_execute_applies_create_task_over_mcp():
    """Approved create_task no longer bridges to pm-agent — agent_execute applies
    the batch directly over the Redmine MCP (one create_redmine_issue per item),
    and the turn finishes terminally."""
    ts = _ApplyToolset()
    execute = chat_graph.make_agent_execute(object(), tools=ts)
    items = [
        {"subject": "a", "assignee": "Hiếu"},
        {"subject": "b", "assignee": "Mai"},
    ]
    out = await execute({
        "user_id": UID,
        "pending_tool": {"id": "tc1", "name": "create_task",
                         "args": {"project": "GIP", "items": items}},
        "user_decision": {"action": "approved", "reason": "gấp nhé"},
        "agent_messages": [],
    })
    assert out["agent_route"] == "finish"
    assert out["tool_result"]["status"] == "redmine_apply"
    created = [c for c in ts.calls if c["name"] == "create_redmine_issue"]
    assert [c["args"]["subject"] for c in created] == ["a", "b"]
    assert "2/2" in out["final_reply"]
