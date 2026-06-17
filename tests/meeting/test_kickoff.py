"""Role-persona proactive kickoff — pure helpers.

`role_data_plan` maps a role's `data_plan` to the set of Redmine MCP read tools
to run; `build_kickoff_messages` assembles the kickoff LLM prompt. Both are pure
(no I/O), so they're unit-tested directly.
"""
from __future__ import annotations

from src.services.kickoff import build_kickoff_messages, role_data_plan


# ─── role_data_plan ───────────────────────────────────────────────────

def test_role_data_plan_own_tasks_reads_assignee_workload():
    assert role_data_plan("own_tasks") == ["get_workload_by_assignee"]


def test_role_data_plan_cross_project_reads_issues_and_unassigned():
    assert role_data_plan("cross_project") == [
        "list_redmine_issue",
        "get_unassigned_issues",
    ]


def test_role_data_plan_minimal_reads_nothing():
    assert role_data_plan("minimal") == []


def test_role_data_plan_unknown_defaults_to_minimal():
    # An unknown/None data_plan must never fetch data (never block, never guess).
    assert role_data_plan("totally-unknown") == []
    assert role_data_plan(None) == []


# ─── build_kickoff_messages ───────────────────────────────────────────

def test_build_kickoff_messages_embeds_all_slots():
    msgs = build_kickoff_messages(
        user_name="Anh",
        role_name="AI Applied",
        role_description="Nghiên cứu và ứng dụng mô hình AI vào sản phẩm.",
        role_kickoff_prompt="Tập trung vào công việc riêng của người dùng.",
        role_data="3 task đang mở trong project Mee.",
    )
    assert isinstance(msgs, list) and len(msgs) == 1
    msg = msgs[0]
    assert msg["role"] == "system"
    content = msg["content"]
    # every runtime slot must appear in the assembled prompt
    assert "Anh" in content
    assert "AI Applied" in content
    assert "Nghiên cứu và ứng dụng mô hình AI vào sản phẩm." in content
    assert "Tập trung vào công việc riêng của người dùng." in content
    assert "3 task đang mở trong project Mee." in content
    assert "Mee" in content  # persona anchor


def test_build_kickoff_messages_no_data_keeps_anti_fabrication_guard():
    # No fetched data → the prompt must still forbid inventing numbers.
    msgs = build_kickoff_messages(
        user_name="Anh",
        role_name="L&D Executive",
        role_description="Đào tạo & phát triển nhân sự.",
        role_kickoff_prompt="Chào ngắn gọn, mời người dùng bắt đầu.",
        role_data="",
    )
    content = msgs[0]["content"].lower()
    assert "không bịa" in content
