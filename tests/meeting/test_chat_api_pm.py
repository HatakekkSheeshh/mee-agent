"""
Task 5 — the chat API maps interrupts and resume decisions correctly for the
pm-agent branch, without regressing the local-tool path.

The shared remote DB is unavailable in this environment (see HANDOFF.md), so
rather than spin up a FastAPI TestClient against a live DB we unit-test the
pure helpers the endpoints delegate to, and tie them to the graph's
_decision_to_payload so the API → graph contract is proven end-to-end.
Endpoint-level live verification is deferred to the manual smoke (Task 6).
"""
from __future__ import annotations

from src.api.chat import (
    PM_TOOL_NAME,
    _approve_decision,
    _persist_fields,
    _reject_decision,
)
from src.graphs.chat_graph import _decision_to_payload


# ─── _persist_fields: distinguish pm interrupt vs local-tool interrupt ──

def test_persist_fields_pm_need_approval():
    pa = {
        "kind": "need_approval",
        "issues": [{"actions": "CREATE", "subject": "Deploy v1"}],
        "prompt": "Xác nhận tạo issue?",
    }
    fields = _persist_fields(pa)
    assert fields["tool_name"] == PM_TOOL_NAME
    assert fields["tool_args"] == pa
    assert fields["response"]["tool"] == PM_TOOL_NAME
    assert fields["response"]["kind"] == "need_approval"
    assert fields["response"]["issues"] == pa["issues"]


def test_persist_fields_pm_need_more_info():
    pa = {"kind": "need_more_info", "prompt": "Issue thuộc project nào?"}
    fields = _persist_fields(pa)
    assert fields["tool_name"] == PM_TOOL_NAME
    assert fields["response"]["kind"] == "need_more_info"


def test_persist_fields_local_tool():
    pa = {
        "tool": "send_email",
        "args": {"to": "a@b.com"},
        "rationale": "user asked",
        "description": "send an email",
    }
    fields = _persist_fields(pa)
    assert fields["tool_name"] == "send_email"
    assert fields["tool_args"] == {"to": "a@b.com"}
    assert fields["response"]["tool"] == "send_email"
    assert fields["response"]["description"] == "send an email"


# ─── _approve_decision ─────────────────────────────────────────────────

def test_approve_decision_pm_approve():
    d = _approve_decision(
        PM_TOOL_NAME, approval_action="approve", text=None, edited_args=None, reason=None
    )
    assert d == {"approval_action": "approve"}
    # … and it maps to an approval payload in the graph.
    assert _decision_to_payload(d)["kind"] == "approval"
    assert _decision_to_payload(d)["approval_action"] == "approve"


def test_approve_decision_pm_more_info_text():
    d = _approve_decision(
        PM_TOOL_NAME, approval_action=None, text="project Mee", edited_args=None, reason=None
    )
    assert d == {"text": "project Mee"}
    # Free text (no approval verb) → a text payload the next pm_call sends.
    payload = _decision_to_payload(d)
    assert payload == {"kind": "text", "text": "project Mee"}


def test_approve_decision_pm_default_is_approve():
    d = _approve_decision(
        PM_TOOL_NAME, approval_action=None, text=None, edited_args=None, reason=None
    )
    assert d == {"approval_action": "approve"}


def test_approve_decision_local_tool_unchanged():
    d = _approve_decision(
        "send_email", approval_action=None, text=None, edited_args={"to": "x"}, reason="ok"
    )
    assert d == {"action": "approved", "edited_args": {"to": "x"}, "reason": "ok"}


# ─── _reject_decision ──────────────────────────────────────────────────

def test_reject_decision_pm():
    d = _reject_decision(PM_TOOL_NAME, reason="không cần nữa")
    assert d == {"approval_action": "reject", "approval_input": "không cần nữa"}
    assert _decision_to_payload(d)["approval_action"] == "reject"


def test_reject_decision_local_tool_unchanged():
    d = _reject_decision("send_email", reason="nope")
    assert d == {"action": "rejected", "reason": "nope"}
