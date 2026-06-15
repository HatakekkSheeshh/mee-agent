"""jobTitle → roles.name resolution (pure).

`resolve_role` normalizes a free-text Entra jobTitle and matches it against each
role's `name` (implicit alias) + its `aliases`. Unknown → None (a wrong role
pulls the wrong data_plan, so we never guess). No seniority stripping — pool
names deliberately contain Lead…/Associate….
"""
from __future__ import annotations

from types import SimpleNamespace

from meeting.services.role_mapping import normalize, resolve_role


def _role(name, aliases=()):
    return SimpleNamespace(name=name, aliases=list(aliases))


ROLES = [
    _role("AI Applied", ["Applied AI", "Applied AI Engineer", "Applied AI Intern"]),
    _role("AI Engineer", ["AI Engineer"]),
    _role("Lead System Engineer"),  # name-only match, no aliases
    _role("Software Engineer", ["Senior Software Engineer"]),
]


def test_normalize_lowercases_and_collapses_punctuation():
    assert normalize("  Applied-AI   Engineer ") == "applied ai engineer"
    assert normalize("L&D Executive") == "l d executive"
    assert normalize(None) == ""


def test_alias_hit_returns_role_name():
    assert resolve_role("Applied AI Engineer", ROLES) == "AI Applied"


def test_word_order_variance_via_alias():
    # Entra "Applied AI" maps to pool "AI Applied" only because it's an alias.
    assert resolve_role("applied ai", ROLES) == "AI Applied"


def test_role_name_itself_is_an_implicit_alias():
    assert resolve_role("lead system engineer", ROLES) == "Lead System Engineer"


def test_case_and_whitespace_insensitive():
    assert resolve_role("  SOFTWARE   engineer ", ROLES) == "Software Engineer"


def test_unknown_title_returns_none():
    assert resolve_role("Chief Vibes Officer", ROLES) is None


def test_blank_or_none_returns_none():
    assert resolve_role("", ROLES) is None
    assert resolve_role(None, ROLES) is None
