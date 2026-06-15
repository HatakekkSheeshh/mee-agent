"""Tests for the event-driven sync runner wiring (network/DB-free).

Covers the guards that keep the write-path hooks safe:
  - sync_project no-ops when AgentBase is unconfigured (MEMORY_ID unset);
  - schedule_project_sync is a safe no-op off-loop / when disabled, and schedules
    a task when a loop is running + MEMORY_ID is set.
The per-project decision logic itself is covered by test_memory_sync.py
(sync_one_project / plan_project_sync).
"""
from __future__ import annotations

import asyncio

import pytest

from meeting.services import memory_sync_runner as runner


@pytest.mark.asyncio
async def test_sync_project_disabled_without_memory_id(monkeypatch):
    monkeypatch.delenv("MEMORY_ID", raising=False)

    # DB must never be touched when AgentBase is disabled.
    async def boom(*a, **k):
        raise AssertionError("repo must not be called when disabled")

    monkeypatch.setattr(runner.repo, "get_meeting", boom)
    result = await runner.sync_project(session=object(), meeting_id="abc")
    assert result == {"action": "disabled"}


def test_schedule_noop_without_memory_id(monkeypatch):
    monkeypatch.delenv("MEMORY_ID", raising=False)
    assert runner.schedule_project_sync("mid") is False


def test_schedule_noop_without_meeting_id(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    assert runner.schedule_project_sync(None) is False


def test_schedule_noop_off_event_loop(monkeypatch):
    # No running loop (sync context) → cannot schedule, must not raise.
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    assert runner.schedule_project_sync("mid") is False


@pytest.mark.asyncio
async def test_schedule_runs_background_sync_on_loop(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-1")
    seen = {}

    async def fake_bg(meeting_id):
        seen["mid"] = meeting_id

    monkeypatch.setattr(runner, "_run_project_sync_bg", fake_bg)
    assert runner.schedule_project_sync("mid-42") is True
    await asyncio.sleep(0)  # let the scheduled task run
    assert seen["mid"] == "mid-42"
