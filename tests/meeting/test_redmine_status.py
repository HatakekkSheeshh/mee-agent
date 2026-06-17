"""Pure status-envelope builder for GET /api/redmine/status."""
from src.api.redmine import (
    EXPECTED_REDMINE_TOOL_COUNT,
    _pm_agent_configured,
    build_redmine_status,
)


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


def test_tools_ok_is_a_floor_not_exact():
    # The live MCP surface can grow; more tools than the floor is still OK.
    s = build_redmine_status(
        key_present=True, registered_tool_count=EXPECTED_REDMINE_TOOL_COUNT + 1,
        pm_agent_ok=True, gate_url=None,
    )
    assert s["redmine_tools_ok"] is True
    assert s["all_ok"] is True


def test_pm_agent_configured_needs_url_and_identity_key(monkeypatch):
    # pm-agent auths via the same agent-identity key → URL + key_present.
    monkeypatch.setenv("PM_AGENT_URL", "https://pm.example/a2a")
    assert _pm_agent_configured(True) is True
    assert _pm_agent_configured(False) is False


def test_pm_agent_configured_false_without_url(monkeypatch):
    monkeypatch.delenv("PM_AGENT_URL", raising=False)
    assert _pm_agent_configured(True) is False
