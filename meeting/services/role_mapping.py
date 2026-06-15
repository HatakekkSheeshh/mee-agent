"""Pure jobTitle → roles.name resolution.

Free-text Entra `jobTitle` strings vary by word order ("Applied AI" ↔ "AI
Applied") and carry extra/seniority words. We do NOT strip seniority
algorithmically — several pool names deliberately contain "Lead …"/"Associate
…", so stripping would corrupt them. Variance is handled by explicit per-role
`aliases` (seed data). `normalize` only absorbs case/punctuation/whitespace
noise. Unknown title → None (never guess: a wrong role pulls the wrong
data_plan). See docs/superpowers/specs/2026-06-14-oid-role-persona-design.md.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(title: str | None) -> str:
    """Lowercase, collapse any run of non-alphanumerics to a single space."""
    return _NON_ALNUM.sub(" ", (title or "").lower()).strip()


def resolve_role(job_title: str | None, roles) -> str | None:
    """Return the matching role's name, or None.

    `roles` is any iterable of objects with `.name: str` and `.aliases:
    list[str]`. Matches normalized `job_title` against each role's normalized
    name (implicit alias) + its normalized aliases.
    """
    target = normalize(job_title)
    if not target:
        return None
    for role in roles:
        candidates = [role.name, *(getattr(role, "aliases", None) or [])]
        if any(normalize(c) == target for c in candidates):
            return role.name
    return None
