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
