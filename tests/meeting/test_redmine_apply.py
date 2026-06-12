"""create_task → MCP apply: item→args mapping, summary, and full-graph flow."""
from __future__ import annotations

from meeting.graphs import _chat_serde as serde


# ── Task 4: pure item→Redmine-args helpers ──────────────────────────
# LIVE-SCHEMA CORRECTION (probe 2026-06-12): create_redmine_issue AND
# update_redmine_issue both expose `due_date` as a real field, so it is passed
# DIRECTLY (not folded into description/notes as the original plan assumed).
def test_create_args_defaults_tracker_and_passes_due_date_directly():
    args = serde.redmine_create_args(
        "GIP",
        {"subject": "viết migration", "assignee": "Hiếu", "due_date": "10/01/2026", "description": "schema"},
    )
    assert args["project_name"] == "GIP"
    assert args["subject"] == "viết migration"
    assert args["tracker"] == "Task"            # default when item has none
    assert args["assigned_to"] == "Hiếu"
    assert args["description"] == "schema"      # due_date NOT folded in
    assert args["due_date"] == "10/01/2026"     # passed as a real field


def test_create_args_respects_explicit_tracker():
    args = serde.redmine_create_args("GIP", {"subject": "x", "tracker": "Bug"})
    assert args["tracker"] == "Bug"


def test_create_args_omits_absent_optionals():
    args = serde.redmine_create_args("GIP", {"subject": "x", "assignee": "Mai"})
    assert "due_date" not in args
    assert "description" not in args


def test_update_args_includes_only_present_fields():
    args = serde.redmine_update_args("GIP", {"subject": "new", "due_date": "12/01"}, "123")
    assert args["issue_id"] == "123"
    assert args["project_name"] == "GIP"
    assert args["subject"] == "new"
    assert args["due_date"] == "12/01"          # direct field, not folded
    assert "assigned_to" not in args            # absent in item → omitted
    assert "notes" not in args                  # no description → no note


def test_update_args_maps_description_to_notes():
    args = serde.redmine_update_args("GIP", {"description": "đã làm xong phần A"}, "9")
    assert args["notes"] == "đã làm xong phần A"


def test_summary_counts_ok_and_lists_failures():
    results = [
        {"subject": "a", "result": {"id": 1}},
        {"subject": "b", "result": {"error": "no assignee"}},
    ]
    text = serde.summarize_redmine_apply("GIP", results)
    assert "1/2" in text
    assert "GIP" in text
    assert "b" in text and "no assignee" in text
