"""_upsert_user resolves the O365 jobTitle to a role and persists role_id.

The role resolver + role lookup are monkeypatched (no DB); we assert the user
row gets the resolved role_id, on both the create and the returning-user paths.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import src.auth.routes as routes
from src.auth.base import UserInfo


ROLE_ID = uuid.uuid4()


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _Session:
    """Minimal fake AsyncSession: returns a preset existing user, records adds."""

    def __init__(self, existing=None):
        self._existing = existing
        self.added = []

    async def execute(self, stmt):
        return _Result(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def _patch_resolution(monkeypatch):
    async def fake_resolve(session, title):
        return "Software Engineer" if title == "Software Engineer" else None
    async def fake_get_role(session, name):
        return SimpleNamespace(id=ROLE_ID, name=name) if name else None
    monkeypatch.setattr(routes.repo, "resolve_role_by_title", fake_resolve)
    monkeypatch.setattr(routes.repo, "get_role", fake_get_role)


async def test_upsert_new_user_sets_role_id(monkeypatch):
    _patch_resolution(monkeypatch)
    info = UserInfo(email="se@vng.com.vn", display_name="SE", position="Software Engineer")
    session = _Session(existing=None)
    user = await routes._upsert_user(session, info)
    assert user.role_id == ROLE_ID


async def test_upsert_returning_user_refreshes_role_id(monkeypatch):
    _patch_resolution(monkeypatch)
    existing = SimpleNamespace(
        display_name="old", avatar_url=None, ms_oid="o", ms_tenant_id="t",
        refresh_token=None, role_id=None, last_login_at=None,
    )
    info = UserInfo(email="se@vng.com.vn", display_name="SE", position="Software Engineer")
    session = _Session(existing=existing)
    user = await routes._upsert_user(session, info)
    assert user.role_id == ROLE_ID


async def test_upsert_unknown_title_leaves_role_id_none(monkeypatch):
    _patch_resolution(monkeypatch)
    info = UserInfo(email="x@vng.com.vn", display_name="X", position="Wizard")
    session = _Session(existing=None)
    user = await routes._upsert_user(session, info)
    assert user.role_id is None


async def test_upsert_persists_position(monkeypatch):
    _patch_resolution(monkeypatch)
    info = UserInfo(email="se@vng.com.vn", display_name="SE", position="Senior Backend Engineer")
    session = _Session(existing=None)
    user = await routes._upsert_user(session, info)
    assert user.position == "Senior Backend Engineer"


async def test_upsert_returning_user_persists_position(monkeypatch):
    _patch_resolution(monkeypatch)
    existing = SimpleNamespace(
        display_name="old", avatar_url=None, ms_oid="o", ms_tenant_id="t",
        refresh_token=None, role_id=None, position=None, last_login_at=None,
    )
    info = UserInfo(email="se@vng.com.vn", display_name="SE", position="Staff Engineer")
    session = _Session(existing=existing)
    user = await routes._upsert_user(session, info)
    assert user.position == "Staff Engineer"
