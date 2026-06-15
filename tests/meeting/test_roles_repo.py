"""Roles pool — ORM model schema + repo accessors.

`roles` is the authoritative, enumerable catalog of role personas. The model
locks the column contract; `get_role`/`list_roles` are thin async accessors,
tested against a fake AsyncSession (no live DB), mirroring test_repo_recordings.
"""
from __future__ import annotations

from types import SimpleNamespace

from meeting.db import models, repositories as repo


# ─── model schema contract ────────────────────────────────────────────

def test_role_model_has_expected_columns():
    cols = {c.name for c in models.Role.__table__.columns}
    assert {
        "id",
        "name",
        "description",
        "data_plan",
        "kickoff_prompt",
        "aliases",
        "created_at",
    } <= cols


def test_role_name_is_unique():
    assert models.Role.__table__.c.name.unique is True


# ─── fake AsyncSession ────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeResult(self._rows)


# ─── get_role ─────────────────────────────────────────────────────────

async def test_get_role_returns_matching_role():
    role = SimpleNamespace(name="AI Applied", data_plan="own_tasks")
    out = await repo.get_role(_FakeSession([role]), "AI Applied")
    assert out is role


async def test_get_role_missing_returns_none():
    out = await repo.get_role(_FakeSession([]), "Nope")
    assert out is None


# ─── list_roles ───────────────────────────────────────────────────────

async def test_list_roles_returns_all_rows():
    roles = [SimpleNamespace(name="AI Applied"), SimpleNamespace(name="BA")]
    out = await repo.list_roles(_FakeSession(roles))
    assert out == roles


async def test_list_roles_empty_returns_empty():
    out = await repo.list_roles(_FakeSession([]))
    assert out == []


# ─── resolve_role_by_title ────────────────────────────────────────────

async def test_resolve_role_by_title_matches_alias():
    roles = [
        SimpleNamespace(name="AI Applied", aliases=["Applied AI Engineer"]),
        SimpleNamespace(name="Software Engineer", aliases=[]),
    ]
    out = await repo.resolve_role_by_title(_FakeSession(roles), "Applied AI Engineer")
    assert out == "AI Applied"


async def test_resolve_role_by_title_unknown_returns_none():
    roles = [SimpleNamespace(name="AI Applied", aliases=[])]
    out = await repo.resolve_role_by_title(_FakeSession(roles), "Nope")
    assert out is None


# ─── User.role_id FK contract ─────────────────────────────────────────

def test_user_model_has_role_id_fk():
    from meeting.db import models as m
    cols = {c.name for c in m.User.__table__.columns}
    assert "role_id" in cols
    # FK targets roles.id
    fks = {fk.column.table.name for c in m.User.__table__.columns for fk in c.foreign_keys}
    assert "roles" in fks
