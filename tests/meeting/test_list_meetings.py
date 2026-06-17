"""list_meetings tool + meeting-vs-project terminology.

The bug: asked "what meetings do I have", the agent called `get_redmine_projects`
(a Redmine tool) because (a) there was no tool to list the user's Mee meetings and
(b) the Mee tools' descriptions said "project", colliding with Redmine's "project".

Split the terms: a *meeting* (cuộc họp) is a Mee container (recordings + MoM); a
*project* is the Redmine concept. `list_meetings` enumerates the user's meetings.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.services.tools import list_meetings as lm
from src.services.tools._registry import get_tool

# Importing the tools package registers every @tool (no network for Redmine defs).
import src.services.tools  # noqa: F401,E402


def test_list_meetings_is_registered():
    assert get_tool("list_meetings") is not None


def test_list_meetings_description_is_meeting_not_redmine_project():
    d = get_tool("list_meetings")["description"].lower()
    assert "meeting" in d or "cuộc họp" in d
    # Must steer away from Redmine so the agent stops grabbing get_redmine_projects.
    assert "redmine" in d


def test_mee_read_tools_never_call_a_meeting_a_project():
    # Mee tools describe MEETINGS; the word "project" may appear ONLY in the
    # disambiguating phrase "redmine project" (e.g. "NOT a Redmine project"),
    # never as a label for the meeting itself ("current project", "the project").
    for name in ("list_meetings", "list_recordings", "switch_meeting"):
        spec = get_tool(name)
        assert spec is not None, name
        residual = (
            spec["description"].lower()
            .replace("get_redmine_projects", "")  # naming the Redmine tool is fine
            .replace("redmine project", "")        # "NOT a Redmine project" is fine
        )
        assert "project" not in residual, name


async def test_list_meetings_returns_user_meetings(monkeypatch):
    pinned = SimpleNamespace(id=uuid.uuid4(), title="AI Innovation Projects", is_pinned=True)
    other = SimpleNamespace(id=uuid.uuid4(), title="GIP", is_pinned=False)

    async def fake_list(session, user_id):
        # repo already sorts (pinned first) AND filters deleted_at IS NULL.
        return [pinned, other]

    monkeypatch.setattr(lm.repo, "list_meetings_for_user", fake_list)

    out = await lm.list_meetings({}, session=object(), user_id=uuid.uuid4())
    assert out["status"] == "ok"
    assert out["count"] == 2
    assert [m["title"] for m in out["meetings"]] == ["AI Innovation Projects", "GIP"]
    assert all("id" in m and "title" in m for m in out["meetings"])
