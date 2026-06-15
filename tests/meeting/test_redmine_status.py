"""Pure status-envelope builder for GET /api/redmine/status."""
from meeting.api.redmine import EXPECTED_REDMINE_TOOL_COUNT, build_redmine_status


def test_all_ok_no_banner_no_gate():
    s = build_redmine_status(
        key_present=True, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT,
        pm_agent_ok=True, gate_url=None,
    )
    assert s["redmine_key_present"] is True
    assert s["redmine_tools_ok"] is True
    assert s["pm_agent_ok"] is True
    assert s["gate_url"] is None
    assert s["all_ok"] is True


def test_missing_key_sets_gate_and_not_ok():
    s = build_redmine_status(
        key_present=False, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT,
        pm_agent_ok=True, gate_url="https://consent/x",
    )
    assert s["redmine_key_present"] is False
    assert s["redmine_tools_ok"] is False  # no key → tools cannot work
    assert s["gate_url"] == "https://consent/x"
    assert s["all_ok"] is False


def test_tool_count_mismatch_reported():
    s = build_redmine_status(
        key_present=True, registered_tool_count=3,
        pm_agent_ok=True, gate_url=None,
    )
    assert s["redmine_tools_ok"] is False
    assert s["registered_tool_count"] == 3
    assert s["expected_tool_count"] == EXPECTED_REDMINE_TOOL_COUNT
    assert s["all_ok"] is False


def test_pm_agent_down_flags_not_ok_but_no_gate():
    s = build_redmine_status(
        key_present=True, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT,
        pm_agent_ok=False, gate_url=None,
    )
    assert s["pm_agent_ok"] is False
    assert s["all_ok"] is False
    assert s["gate_url"] is None  # pm-agent is not key-fixable
