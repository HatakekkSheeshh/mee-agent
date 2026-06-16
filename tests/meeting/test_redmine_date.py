"""Pure due-date normalizer for the Redmine MCP (always YYYY-MM-DD or None).

create_redmine_issue / update_redmine_issue require `due_date` as YYYY-MM-DD.
Free-text from the create_task card or a MoM-derived deadline (DD/MM/YYYY,
"Chưa xác định", …) must be normalized or dropped before it reaches the MCP.
"""
from __future__ import annotations

import pytest

from meeting.graphs._chat_serde import to_redmine_date


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("06/06/2026", "2026-06-06"),     # DD/MM/YYYY (VI day-first)
        ("6/6/2026", "2026-06-06"),       # D/M/YYYY non-padded
        ("10/01/2026", "2026-01-10"),     # day-first: 10 Jan, not 1 Oct
        ("2026-06-06", "2026-06-06"),     # already ISO → passthrough
        ("06-06-2026", "2026-06-06"),     # DD-MM-YYYY dashes
        ("  06/06/2026  ", "2026-06-06"), # surrounding whitespace tolerated
    ],
)
def test_parses_known_formats_to_iso(raw, expected):
    assert to_redmine_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Chưa xác định",   # MoM placeholder, not a date
        "",                # empty
        "   ",             # whitespace only
        None,              # missing
        "12/01",           # no year → ambiguous, dropped
        "2026-13-01",      # invalid month
        "32/01/2026",      # invalid day
        "next week",       # free text
    ],
)
def test_unparseable_returns_none(raw):
    assert to_redmine_date(raw) is None
