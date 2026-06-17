# OID → Position → Role Persona Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At O365 login, fetch the user's `jobTitle` from Microsoft Graph, map it to a `roles.name`, persist it on `users.role_id`, and make the chat kickoff use the logged-in user's real role.

**Architecture:** A pure mapping (`resolve_role`) over a new `roles.aliases` column resolves a free-text `jobTitle` to a canonical pool name; `_upsert_user` persists the resolved `role_id` at login (same txn); the kickoff endpoint reads the authenticated user's role instead of an env var. No `memory_client` actor change (project_facts stays shared; per-user persona is Feature 2).

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), Alembic (psycopg2 sync), MSAL + Microsoft Graph, pytest (`asyncio_mode=auto`), React/TS frontend.

**Spec:** `docs/superpowers/specs/2026-06-14-oid-role-persona-design.md`

**Test convention:** suite is `tests/meeting/`, `asyncio_mode=auto` (async tests need no decorator). Run with `venv/bin/pytest`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `alembic/versions/0016_roles_pool.py` → `0019_roles_pool.py` | Re-parent to fix duplicate `0016` | Rename + edit |
| `alembic/versions/0020_user_role_and_aliases.py` | Add `users.role_id`, `roles.aliases`; reseed aliases | Create |
| `meeting/services/role_mapping.py` | Pure `normalize` + `resolve_role(job_title, roles)` | Create |
| `meeting/db/models.py` | `Role.aliases` column; `User.role_id` FK + `role` relationship | Modify |
| `meeting/db/repositories.py` | `resolve_role_by_title(session, title)` | Modify |
| `meeting/db/seed_roles.py` | Add `aliases` key to each seed role | Modify |
| `meeting/auth/base.py` | `UserInfo.position`, `UserInfo.department` | Modify |
| `meeting/auth/microsoft.py` | `fetch_profile()` + wire into `exchange_code` | Modify |
| `meeting/auth/routes.py` | `_upsert_user` resolves + persists `role_id` | Modify |
| `meeting/api/chat.py` | kickoff reads authenticated `user.role` | Modify |
| `meeting_frontend_react/src/components/ChatPane.tsx` | Stop requiring `VITE_KICKOFF_ROLE` | Modify |
| `tests/meeting/test_role_mapping.py` | `resolve_role` unit tests | Create |
| `tests/meeting/test_roles_repo.py` | `aliases` col + `resolve_role_by_title` | Modify |
| `tests/meeting/test_seed_roles.py` | aliases present | Modify |
| `tests/meeting/test_auth_microsoft.py` | `fetch_profile` + degrade | Modify |
| `tests/meeting/test_auth_role_persist.py` | `_upsert_user` sets `role_id` | Create |
| `tests/meeting/test_kickoff_role_source.py` | `_pick_role_name` (semantics unchanged) | Modify |

---

## Task 0: Fix the duplicate-`0016` migration (prerequisite — alembic is unrunnable until done)

**Files:**
- Rename: `alembic/versions/0016_roles_pool.py` → `alembic/versions/0019_roles_pool.py`

The merge left two migrations with `revision = "0016"`. Alembic errors on every command until this is resolved. Re-parent the standalone `roles` table to the end of the chain.

- [ ] **Step 1: Rename the file**

```bash
git mv alembic/versions/0016_roles_pool.py alembic/versions/0019_roles_pool.py
```

- [ ] **Step 2: Edit the revision identifiers and docstring**

In `alembic/versions/0019_roles_pool.py` change exactly these two lines:

```python
revision: str = "0019"
down_revision: Union[str, None] = "0018"
```

And update the docstring's first line + `Revision ID:` for accuracy:

```python
"""roles pool — role-persona kickoff catalog + seed

Revision ID: 0019
Revises: 0018
```

- [ ] **Step 3: Verify a single linear head (no DB needed)**

Run: `venv/bin/alembic heads`
Expected: exactly **one** head — `0019 (head)`. If `alembic` complains about a missing `DATABASE_URL`, set `DATABASE_URL_SYNC` first (see Task 11).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0019_roles_pool.py
git commit -m "fix(alembic): re-parent roles_pool 0016→0019 to resolve dup revision id"
```

---

## Task 1: `resolve_role` pure mapping

**Files:**
- Create: `meeting/services/role_mapping.py`
- Test: `tests/meeting/test_role_mapping.py`

- [ ] **Step 1: Write the failing tests**

`tests/meeting/test_role_mapping.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/meeting/test_role_mapping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'meeting.services.role_mapping'`

- [ ] **Step 3: Write the implementation**

`meeting/services/role_mapping.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/meeting/test_role_mapping.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add meeting/services/role_mapping.py tests/meeting/test_role_mapping.py
git commit -m "feat(chat): pure resolve_role jobTitle→role mapping"
```

---

## Task 2: `Role.aliases` column + `resolve_role_by_title` repo + seed aliases

**Files:**
- Modify: `meeting/db/models.py` (the `Role` class, ~line 76-87)
- Modify: `meeting/db/repositories.py` (roles section, after `list_roles` ~line 63)
- Modify: `meeting/db/seed_roles.py` (add `aliases` to each dict)
- Test: `tests/meeting/test_roles_repo.py` (modify)

- [ ] **Step 1: Write the failing tests**

In `tests/meeting/test_roles_repo.py`, update the column contract test and add a `resolve_role_by_title` test. Change `test_role_model_has_expected_columns` to include `"aliases"`:

```python
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
```

Append these tests at the end of the file (the `_FakeSession`/`_FakeResult` helpers already exist in this file):

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/meeting/test_roles_repo.py -v`
Expected: FAIL — `test_role_model_has_expected_columns` (no `aliases` col) and `AttributeError: module 'meeting.db.repositories' has no attribute 'resolve_role_by_title'`

- [ ] **Step 3a: Add the `aliases` column to the `Role` model**

In `meeting/db/models.py`, add the import at the top if missing:

```python
from sqlalchemy import ARRAY
```

In the `Role` class, add after the `kickoff_prompt` column:

```python
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}", default=list
    )
```

- [ ] **Step 3b: Add `resolve_role_by_title` to the repo**

In `meeting/db/repositories.py`, add the import at the top if missing:

```python
from meeting.services.role_mapping import resolve_role
```

Add after `list_roles`:

```python
async def resolve_role_by_title(session: AsyncSession, title: str | None) -> Optional[str]:
    """Resolve a free-text jobTitle to a canonical roles.name, or None.

    Loads the pool and delegates to the pure `resolve_role`. None on no match
    (the caller leaves role_id NULL → generic kickoff).
    """
    roles = await list_roles(session)
    return resolve_role(title, roles)
```

- [ ] **Step 3c: Add `aliases` to every seed role**

In `meeting/db/seed_roles.py`, add an `"aliases": [...]` key to each of the 10 role dicts. Seed empty lists for now (real Entra strings get filled later — the role `name` already matches as an implicit alias). Example for the first role:

```python
    {
        "name": "AI Applied",
        "data_plan": "own_tasks",
        "aliases": [],
        "description": (
            ...
        ),
        "kickoff_prompt": (
            ...
        ),
    },
```

Add `"aliases": []` to all 10 dicts. (When the real Entra `jobTitle` strings arrive, populate these lists; the reseed UPDATE in Task 4 pushes them to the DB.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/meeting/test_roles_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meeting/db/models.py meeting/db/repositories.py meeting/db/seed_roles.py tests/meeting/test_roles_repo.py
git commit -m "feat(chat): roles.aliases column + resolve_role_by_title repo"
```

---

## Task 3: `User.role_id` FK + `role` relationship

**Files:**
- Modify: `meeting/db/models.py` (the `User` class, ~line 36-65)
- Test: `tests/meeting/test_roles_repo.py` (add a model-contract test)

- [ ] **Step 1: Write the failing test**

Append to `tests/meeting/test_roles_repo.py`:

```python
def test_user_model_has_role_id_fk():
    from meeting.db import models as m
    cols = {c.name for c in m.User.__table__.columns}
    assert "role_id" in cols
    # FK targets roles.id
    fks = {fk.column.table.name for c in m.User.__table__.columns for fk in c.foreign_keys}
    assert "roles" in fks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_roles_repo.py::test_user_model_has_role_id_fk -v`
Expected: FAIL — `assert "role_id" in cols`

- [ ] **Step 3: Add the column + relationship**

In `meeting/db/models.py`, add the import if missing:

```python
from sqlalchemy import ForeignKey
```

In the `User` class, add after `refresh_token`:

```python
    role_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), nullable=True
    )
```

And add a relationship near the other `User` relationships:

```python
    role: Mapped[Optional["Role"]] = relationship("Role", lazy="selectin")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_roles_repo.py::test_user_model_has_role_id_fk -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meeting/db/models.py tests/meeting/test_roles_repo.py
git commit -m "feat(auth): users.role_id FK + role relationship"
```

---

## Task 4: Alembic `0020` — add `users.role_id`, `roles.aliases`, reseed aliases

**Files:**
- Create: `alembic/versions/0020_user_role_and_aliases.py`

This migration is DB DDL (not unit-tested); it's verified by `alembic heads` + the user running `alembic upgrade head` (Task 11). DDL is idempotent so a re-run is safe.

- [ ] **Step 1: Create the migration**

`alembic/versions/0020_user_role_and_aliases.py`:

```python
"""users.role_id + roles.aliases + alias reseed

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-14

Adds users.role_id (FK→roles.id, nullable — resolved from O365 jobTitle at
login) and roles.aliases (text[], the jobTitle strings that map to each role),
then reseeds aliases from meeting.db.seed_roles by name. The reseed is an UPDATE
by unique name (idempotent).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from meeting.db.seed_roles import SEED_ROLES

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # roles.aliases — text[] default empty.
    op.add_column(
        "roles",
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    # users.role_id — nullable FK → roles.id.
    op.add_column(
        "users",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_role_id", "users", "roles", ["role_id"], ["id"]
    )

    # Reseed aliases by name (idempotent UPDATE).
    update = sa.text("UPDATE roles SET aliases = :aliases WHERE name = :name")
    bind = op.get_bind()
    for r in SEED_ROLES:
        bind.execute(update, {"aliases": r.get("aliases", []), "name": r["name"]})


def downgrade() -> None:
    op.drop_constraint("fk_users_role_id", "users", type_="foreignkey")
    op.drop_column("users", "role_id")
    op.drop_column("roles", "aliases")
```

- [ ] **Step 2: Verify single head**

Run: `venv/bin/alembic heads`
Expected: one head — `0020 (head)`

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/0020_user_role_and_aliases.py
git commit -m "feat(db): migration 0020 — users.role_id + roles.aliases + reseed"
```

---

## Task 5: Graph profile fetch + `UserInfo.position`

**Files:**
- Modify: `meeting/auth/base.py` (the `UserInfo` dataclass)
- Modify: `meeting/auth/microsoft.py` (`fetch_profile` + wire into `exchange_code`)
- Test: `tests/meeting/test_auth_microsoft.py` (modify)

- [ ] **Step 1: Write the failing tests**

In `tests/meeting/test_auth_microsoft.py`, add a Graph stub + tests. The provider's `fetch_profile` calls a module-level `_graph_get_me(access_token)` seam that we monkeypatch (no network). Add:

```python
def test_fetch_profile_parses_graph_response(monkeypatch):
    import meeting.auth.microsoft as ms
    monkeypatch.setattr(
        ms, "_graph_get_me",
        lambda token: {"jobTitle": "Applied AI Engineer", "department": "Engineer"},
    )
    provider = _provider(monkeypatch, {"access_token": "graph-token"})
    prof = provider.fetch_profile("graph-token")
    assert prof["job_title"] == "Applied AI Engineer"
    assert prof["department"] == "Engineer"


def test_fetch_profile_degrades_to_empty_on_graph_error(monkeypatch):
    import meeting.auth.microsoft as ms
    def _boom(token):
        raise RuntimeError("graph 500")
    monkeypatch.setattr(ms, "_graph_get_me", _boom)
    provider = _provider(monkeypatch, {"access_token": "graph-token"})
    assert provider.fetch_profile("graph-token") == {}


def test_exchange_code_sets_position_from_graph(monkeypatch):
    import meeting.auth.microsoft as ms
    monkeypatch.setattr(
        ms, "_graph_get_me",
        lambda token: {"jobTitle": "Software Engineer", "department": "Product"},
    )
    result = {
        "access_token": "graph-token",
        "id_token_claims": {
            "oid": "9c1f8e7a-1111-2222-3333-444455556666",
            "tid": TENANT_ID,
            "preferred_username": "An.Nguyen@VNG.com.vn",
            "name": "An Nguyễn",
        },
    }
    provider = _provider(monkeypatch, result)
    info = provider.exchange_code(code="auth-code-abc", redirect_uri=REDIRECT)
    assert info.position == "Software Engineer"
    assert info.department == "Product"


def test_exchange_code_position_none_when_graph_fails(monkeypatch):
    import meeting.auth.microsoft as ms
    monkeypatch.setattr(ms, "_graph_get_me", lambda token: (_ for _ in ()).throw(RuntimeError("x")))
    result = {
        "access_token": "graph-token",
        "id_token_claims": {
            "oid": "o", "tid": TENANT_ID,
            "preferred_username": "a@vng.com.vn", "name": "A",
        },
    }
    provider = _provider(monkeypatch, result)
    info = provider.exchange_code(code="c", redirect_uri=REDIRECT)
    assert info.position is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/meeting/test_auth_microsoft.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_graph_get_me'` / no `fetch_profile` / `UserInfo` has no `position`.

- [ ] **Step 3a: Extend `UserInfo`**

In `meeting/auth/base.py`, add to the `UserInfo` dataclass (after `ms_token_cache`):

```python
    # O365 profile (Microsoft Graph /me). position = jobTitle, used to resolve
    # the user's role at login. None when Graph is unavailable. department is
    # informational (logging only). Domain is NOT stored (derivable from email).
    position: Optional[str] = None
    department: Optional[str] = None
```

- [ ] **Step 3b: Add `_graph_get_me` + `fetch_profile`, wire into `exchange_code`**

In `meeting/auth/microsoft.py`, add at module level (after `SCOPES`):

```python
import json
import urllib.request

_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me?$select=jobTitle,department"


def _graph_get_me(access_token: str) -> dict:
    """GET Graph /me with the access token. Network seam — monkeypatched in tests."""
    req = urllib.request.Request(
        _GRAPH_ME_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode() or "{}")
```

Add this method to `MicrosoftProvider`:

```python
    def fetch_profile(self, access_token: str) -> dict:
        """Fetch {job_title, department} from Graph /me. Best-effort: any error
        returns {} so login never breaks (the user just gets a generic kickoff).
        """
        try:
            me = _graph_get_me(access_token)
            return {"job_title": me.get("jobTitle"), "department": me.get("department")}
        except Exception as e:  # noqa: BLE001 — best-effort, login must not break
            import logging
            logging.getLogger(__name__).warning("Graph /me fetch failed: %s", e)
            return {}
```

In `exchange_code`, replace the `return UserInfo(...)` block with a profile fetch first:

```python
        token_cache = cache.serialize() if cache.serialize() != "{}" else None

        prof = self.fetch_profile(result.get("access_token") or "")
        return UserInfo(
            email=email,
            display_name=name,
            ms_oid=oid,
            ms_tenant_id=tid,
            ms_token_cache=token_cache,
            position=prof.get("job_title"),
            department=prof.get("department"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/meeting/test_auth_microsoft.py -v`
Expected: PASS (original 2 tests + 4 new)

- [ ] **Step 5: Commit**

```bash
git add meeting/auth/base.py meeting/auth/microsoft.py tests/meeting/test_auth_microsoft.py
git commit -m "feat(auth): fetch jobTitle from Graph /me into UserInfo.position"
```

---

## Task 6: `_upsert_user` persists `role_id`

**Files:**
- Modify: `meeting/auth/routes.py` (`_upsert_user`, ~line 299-336)
- Test: `tests/meeting/test_auth_role_persist.py` (create)

- [ ] **Step 1: Write the failing test**

`tests/meeting/test_auth_role_persist.py`:

```python
"""_upsert_user resolves the O365 jobTitle to a role and persists role_id.

The role resolver + role lookup are monkeypatched (no DB); we assert the user
row gets the resolved role_id, on both the create and the returning-user paths.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import meeting.auth.routes as routes
from meeting.auth.base import UserInfo


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/meeting/test_auth_role_persist.py -v`
Expected: FAIL — the user's `role_id` is not set (current `_upsert_user` ignores `position`).

- [ ] **Step 3: Implement role resolution in `_upsert_user`**

In `meeting/auth/routes.py`, add the import at the top:

```python
from meeting.db import repositories as repo
```

Add a helper above `_upsert_user`:

```python
async def _resolve_role_id(session: AsyncSession, position: Optional[str]):
    """jobTitle → role name → role_id, or None. Best-effort: never raises."""
    if not position:
        return None
    name = await repo.resolve_role_by_title(session, position)
    if not name:
        return None
    role = await repo.get_role(session, name)
    return role.id if role else None
```

In `_upsert_user`, on the **returning-user** branch, before `user.last_login_at = ...`:

```python
        user.role_id = await _resolve_role_id(session, info.position)
```

And on the **new-user** branch, add `role_id=` to the `User(...)` constructor:

```python
    user = User(
        email=info.email,
        display_name=info.display_name,
        avatar_url=info.avatar_url,
        ms_oid=info.ms_oid,
        ms_tenant_id=info.ms_tenant_id,
        refresh_token=encrypt_token(info.ms_token_cache) if info.ms_token_cache else None,
        role_id=await _resolve_role_id(session, info.position),
        voice_enrolled=False,
        last_login_at=datetime.now(timezone.utc),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/meeting/test_auth_role_persist.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add meeting/auth/routes.py tests/meeting/test_auth_role_persist.py
git commit -m "feat(auth): resolve + persist users.role_id at login"
```

---

## Task 7: Kickoff reads the authenticated user's role

**Files:**
- Modify: `meeting/api/chat.py` (`KickoffRequest`/`_pick_role_name` ~line 58-69, `kickoff_session` ~line 251-297)
- Test: `tests/meeting/test_kickoff_role_source.py` (modify docstring only — semantics unchanged)

- [ ] **Step 1: Update the test's docstring (behavior unchanged, source changed)**

In `tests/meeting/test_kickoff_role_source.py`, replace the module docstring (the three assertions stay valid — `_pick_role_name` still: request override wins, else fallback, else None). New docstring:

```python
"""Kickoff role source — the logged-in user's resolved role drives the kickoff.

`_pick_role_name` decides the role name: the optional dev override
(KickoffRequest.role, from VITE_KICKOFF_ROLE) wins; otherwise the user's
resolved role name (users.role_id → roles.name); otherwise None (→ generic
greeting).
"""
```

Run: `venv/bin/pytest tests/meeting/test_kickoff_role_source.py -v`
Expected: still PASS (no logic change). This confirms `_pick_role_name` survives the rewire.

- [ ] **Step 2: Rewire `kickoff_session`**

In `meeting/api/chat.py`:

(a) Update the `KickoffRequest` comment + keep the field (now a dev override):

```python
class KickoffRequest(BaseModel):
    # Optional dev override (VITE_KICKOFF_ROLE). Default path uses the logged-in
    # user's resolved role (users.role_id). Falls back to a generic greeting.
    role: Optional[str] = None
```

(b) Keep `_pick_role_name` but rename the second param for clarity:

```python
def _pick_role_name(
    request_role: Optional[str], user_role: Optional[str]
) -> Optional[str]:
    """The role for a kickoff: the dev override wins, else the user's resolved
    role, else None (→ generic greeting)."""
    return (request_role or "").strip() or user_role
```

(c) Remove the now-unused import. Change line 39 from:

```python
from meeting.memory_client import DEFAULT_ACTOR_ID, get_user_role
```

to (no longer needed — role comes from the DB; `get_user_role` stays in `memory_client` for Feature 2):

```python
# (role now comes from the authenticated user's users.role_id, not AgentBase)
```

Ensure `User` is imported (it is via `from meeting.db.models import ...`; add it to that import if absent). `get_current_user` is already imported at line 26.

(d) Replace the `kickoff_session` signature + body head:

```python
@router.post("/sessions/{session_id}/kickoff")
async def kickoff_session(
    session_id: str,
    req: KickoffRequest = KickoffRequest(),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Mee speaks first: a role-tailored, data-grounded greeting on an empty
    thread. Idempotent — if the thread already has messages, do nothing. Never
    raises on a kickoff failure; `run_kickoff` degrades to a generic greeting.
    """
    sid = _parse_uuid(session_id)
    chat = await repo.get_chat_session(session, sid)
    if not chat:
        raise HTTPException(status_code=404, detail="Session not found")

    existing = await repo.list_chat_messages(session, sid, limit=1)
    if existing:
        return {"reply": None, "skipped": True}

    user_role_name = user.role.name if user.role else None
    role_name = _pick_role_name(req.role, user_role_name)
    role = await repo.get_role(session, role_name) if role_name else None
    user_name = (user.display_name or "").strip() or "bạn"
```

(The rest of the function — `_call_tool`, `_generate`, `run_kickoff`, `add_chat_message`, return — is unchanged.)

- [ ] **Step 3: Run the chat/kickoff test set**

Run: `venv/bin/pytest tests/meeting/test_kickoff_role_source.py tests/meeting/test_kickoff.py tests/meeting/test_kickoff_orchestration.py -v`
Expected: PASS. (`_pick_role_name` tests pass; orchestration/kickoff unit tests untouched.)

- [ ] **Step 4: Commit**

```bash
git add meeting/api/chat.py tests/meeting/test_kickoff_role_source.py
git commit -m "feat(chat): kickoff uses authenticated user's role_id (env role → dev override)"
```

---

## Task 8: Frontend — stop requiring `VITE_KICKOFF_ROLE`

**Files:**
- Modify: `meeting_frontend_react/src/components/ChatPane.tsx` (~line 108-115)

`api.chat.kickoff(sessionId, role?)` already takes an optional role — leave the client method as-is (the dev override still works if someone passes a role). Just stop the component from reading the env var so kickoff works for the logged-in user.

- [ ] **Step 1: Edit `ChatPane.tsx`**

Replace this block:

```typescript
      // v1 has no login: the role is a deploy-time constant from the env
      // (VITE_KICKOFF_ROLE), passed through to the kickoff endpoint.
      const kickoffRole = (
        import.meta as unknown as { env: Record<string, string | undefined> }
      ).env.VITE_KICKOFF_ROLE;
      const res = await api.chat.kickoff(sid, kickoffRole);
```

with:

```typescript
      // Role comes from the logged-in user (users.role_id, resolved from their
      // O365 jobTitle). No env role needed.
      const res = await api.chat.kickoff(sid);
```

- [ ] **Step 2: Typecheck**

Run: `cd meeting_frontend_react && npm run build`
Expected: tsc passes (no unused-var error for the removed `kickoffRole`).

- [ ] **Step 3: Commit**

```bash
git add meeting_frontend_react/src/components/ChatPane.tsx
git commit -m "feat(fe): kickoff uses the logged-in user's role, drop VITE_KICKOFF_ROLE"
```

---

## Task 9: Update `test_seed_roles` for aliases

**Files:**
- Modify: `tests/meeting/test_seed_roles.py`

- [ ] **Step 1: Add an aliases-present assertion**

Add a test asserting every seed role carries an `aliases` list (key present, type list):

```python
def test_every_seed_role_has_aliases_list():
    from meeting.db.seed_roles import SEED_ROLES
    for r in SEED_ROLES:
        assert "aliases" in r
        assert isinstance(r["aliases"], list)
```

- [ ] **Step 2: Run**

Run: `venv/bin/pytest tests/meeting/test_seed_roles.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/meeting/test_seed_roles.py
git commit -m "test(chat): assert seed roles carry an aliases list"
```

---

## Task 10: Full-suite verification

- [ ] **Step 1: Run the whole meeting suite**

Run: `venv/bin/pytest tests/meeting -q`
Expected: all green except the 3 KNOWN pre-existing failures (`test_reconcile_bridge`, `test_redmine_apply` from `b6cd542`'s stale `FakeToolset.build_task_items(description=)`) — unrelated to this work. Confirm no NEW failures.

- [ ] **Step 2: Frontend typecheck**

Run: `cd meeting_frontend_react && npm run build`
Expected: PASS.

---

## Task 11: Apply the migration (user-run)

**This is run by the user** (same DB server; the env already holds the connection string).

- [ ] **Step 1: Confirm single head**

Run: `venv/bin/alembic heads`
Expected: `0020 (head)`

- [ ] **Step 2: Apply**

```bash
# DATABASE_URL_SYNC (psycopg2) or DATABASE_URL is read by alembic/env.py.
venv/bin/alembic upgrade head
```

Expected: applies `0019` (roles table, if not present) + `0020` (role_id, aliases, reseed). Idempotent reseed (UPDATE by name) → safe to re-run.

- [ ] **Step 3: Smoke-check (optional)**

Log in via real O365, open a chat on an empty thread, confirm Mee greets in a role-tailored way for a user whose `jobTitle` matches a seeded role name (or a filled alias). Users with unmatched titles get the generic greeting (expected until aliases are filled).

---

## Self-Review notes

- **Spec coverage:** A (Graph fetch) → Task 5; B (resolve_role) → Task 1; C (model/repo/seed/migration/_upsert_user) → Tasks 2,3,4,6; D (kickoff rewire) → Task 7; E (FE) → Task 8; migration-chain fix → Task 0; tests → each task + Task 10.
- **Deferred / out of scope (Feature 2):** learned-style persona, `actorId`=OID for `user_prefs`, `_agent_system_prompt` injection. `project_facts` stays on the shared actor — untouched.
- **Known pre-existing failures** (`test_reconcile_bridge`, `test_redmine_apply`) are unrelated; Task 10 only requires no NEW failures.
- **Aliases are seeded empty** — the mapping works via implicit role-name match day one; real Entra strings get filled into `seed_roles.py` + pushed via the Task 4 reseed UPDATE (re-run `alembic upgrade` or a one-line UPDATE) when available.
