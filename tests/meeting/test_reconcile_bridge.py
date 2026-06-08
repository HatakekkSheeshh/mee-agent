"""create_task → pm-agent reconcile bridge (2026-06-08 spec)."""
from __future__ import annotations

import uuid

from meeting.graphs import chat_graph


def test_reconcile_text_lists_items_and_project():
    text = chat_graph._reconcile_text("GIP", [
        {"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01/2026"},
        {"subject": "POC caching", "assignee": "", "due_date": ""},
    ])
    assert "GIP" in text
    assert "viết migration" in text
    assert "Hiếu" in text
    assert "10/01/2026" in text
    assert "POC caching" in text


def test_reconcile_text_handles_blank_project():
    text = chat_graph._reconcile_text("", [{"subject": "x"}])
    assert "x" in text
    assert text  # non-empty even with no project


MID = "11111111-1111-1111-1111-111111111111"


async def test_build_template_from_mom_action_items(monkeypatch):
    async def fake_items(session, mid):
        assert mid == uuid.UUID(MID)
        return [{"pic": "Hiếu", "deadline": "10/01", "item": "viết migration"}]

    monkeypatch.setattr(chat_graph.repo, "get_mom_action_items", fake_items)

    tpl = await chat_graph._build_reconcile_template(
        object(), {}, {"title": "AI Innovation Project"}, MID
    )
    assert tpl["project"] == "AI Innovation Project"   # default = meeting title
    assert tpl["items"][0]["subject"] == "viết migration"
    assert tpl["items"][0]["assignee"] == "Hiếu"


async def test_build_template_from_explicit_title():
    tpl = await chat_graph._build_reconcile_template(
        object(),
        {"title": "Deploy v1", "assignee": "Mai", "deadline": "06/06/2026"},
        {"title": "Dự án Mee"}, MID,
    )
    assert tpl["project"] == "Dự án Mee"
    assert len(tpl["items"]) == 1
    assert tpl["items"][0]["subject"] == "Deploy v1"
    assert tpl["items"][0]["due_date"] == "06/06/2026"
