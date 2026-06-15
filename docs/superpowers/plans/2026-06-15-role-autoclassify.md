# Role Auto-Classify (Background) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A background cron worker classifies each unmatched O365 `jobTitle` into the best-fit existing role via an LLM, writes the `jobTitle` back as an alias (self-healing), and backfills `users.role_id`.

**Architecture:** Reuse Feature 1's `resolve_role` + `roles.aliases`. Add `users.position` (persisted at login), a pure-ish `classify_role(job_title, roles, *, generate)` LLM fallback, an `add_role_alias` repo helper, and a standalone script mirroring `scripts/sync_memory.py`.

**Tech Stack:** FastAPI/SQLAlchemy async, Alembic (idempotent migrations), OpenAI-compatible LLM, pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-06-15-role-autoclassify-design.md`
**Builds on:** Feature 1 (`users.role_id`, `roles.aliases`, `meeting/services/role_mapping.py:resolve_role`).

**Test convention:** `tests/meeting/`, `asyncio_mode=auto`; run `venv/bin/pytest`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `meeting/db/models.py` | `User.position` column | Modify |
| `meeting/auth/routes.py` | `_upsert_user` sets `position` | Modify |
| `alembic/versions/0021_users_position.py` | add `users.position` (idempotent) | Create |
| `meeting/services/role_mapping.py` | `classify_role(...)` LLM fallback | Modify |
| `meeting/db/repositories.py` | `add_role_alias(session, role_id, alias)` | Modify |
| `scripts/classify_roles.py` | background worker | Create |
| `tests/meeting/test_role_classify.py` | `classify_role` unit tests | Create |
| `tests/meeting/test_roles_repo.py` | `add_role_alias` tests | Modify |
| `tests/meeting/test_auth_role_persist.py` | `_upsert_user` sets `position` | Modify |

---

## Task 1: `users.position` column + persist at login + migration 0021

**Files:** `meeting/db/models.py`, `meeting/auth/routes.py`, `alembic/versions/0021_users_position.py`, `tests/meeting/test_auth_role_persist.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/meeting/test_auth_role_persist.py` (helpers `_Session`, `_patch_resolution`, `ROLE_ID` already exist there):

```python
async def test_upsert_persists_position(monkeypatch):
    _patch_resolution(monkeypatch)
    info = UserInfo(email="se@vng.com.vn", display_name="SE", position="Senior Backend Engineer")
    session = _Session(existing=None)
    user = await routes._upsert_user(session, info)
    assert user.position == "Senior Backend Engineer"
```

- [ ] **Step 2: Run to verify fail**

Run: `venv/bin/pytest tests/meeting/test_auth_role_persist.py::test_upsert_persists_position -v`
Expected: FAIL (`position` not set / not a column).

- [ ] **Step 3a: Add the column to `User`**

In `meeting/db/models.py`, in the `User` class, after `role_id`:

```python
    position: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

- [ ] **Step 3b: Persist it in `_upsert_user`**

In `meeting/auth/routes.py`, returning-user branch (near where `role_id` is set):

```python
        user.position = info.position
```

New-user branch — add to the `User(...)` constructor:

```python
        position=info.position,
```

- [ ] **Step 3c: Create migration 0021 (idempotent)**

`alembic/versions/0021_users_position.py`:

```python
"""users.position — raw O365 jobTitle (for background role classification)

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")} if insp.has_table("users") else set()
    if "position" not in cols:
        op.add_column("users", sa.Column("position", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "position")
```

- [ ] **Step 4: Run to verify pass + single head**

Run: `venv/bin/pytest tests/meeting/test_auth_role_persist.py -v` → PASS.
Run: `venv/bin/alembic heads` → one head `0021 (head)` (set a dummy `DATABASE_URL` + `DATABASE_URL_SYNC` env first if it complains; `heads` doesn't connect).

- [ ] **Step 5: Commit**

```bash
git add meeting/db/models.py meeting/auth/routes.py alembic/versions/0021_users_position.py tests/meeting/test_auth_role_persist.py
git commit -m "feat(auth): persist users.position (raw jobTitle) + migration 0021"
```

---

## Task 2: `classify_role` LLM fallback

**Files:** `meeting/services/role_mapping.py`, `tests/meeting/test_role_classify.py`

- [ ] **Step 1: Write the failing tests**

`tests/meeting/test_role_classify.py`:

```python
"""classify_role — LLM fallback that maps an unmatched jobTitle into the pool.

`generate(messages) -> str` is injected so no network is needed. Guards: the
returned role name must be in the pool; below the confidence threshold → None;
hallucinated/NONE answers → None.
"""
from __future__ import annotations

from types import SimpleNamespace

from meeting.services.role_mapping import classify_role


def _role(name):
    return SimpleNamespace(name=name, description="", data_plan="own_tasks", aliases=[])


ROLES = [_role("AI Applied"), _role("Software Engineer"), _role("Business Analyst")]


def test_confident_match_returns_pool_role():
    gen = lambda messages: '{"role": "Software Engineer", "confidence": 0.92}'
    assert classify_role("Senior Backend Developer", ROLES, generate=gen) == "Software Engineer"


def test_below_threshold_returns_none():
    gen = lambda messages: '{"role": "Software Engineer", "confidence": 0.3}'
    assert classify_role("Office Cat", ROLES, generate=gen) is None


def test_out_of_pool_name_rejected():
    gen = lambda messages: '{"role": "Chief Vibes Officer", "confidence": 0.99}'
    assert classify_role("Vibes Lead", ROLES, generate=gen) is None


def test_none_answer_returns_none():
    gen = lambda messages: "NONE"
    assert classify_role("Unknown", ROLES, generate=gen) is None


def test_strips_think_and_parses():
    gen = lambda messages: '<think>hmm</think>{"role": "AI Applied", "confidence": 0.8}'
    assert classify_role("Applied AI Researcher", ROLES, generate=gen) == "AI Applied"


def test_llm_garbage_returns_none():
    gen = lambda messages: "I think maybe a software engineer probably?"
    assert classify_role("x", ROLES, generate=gen) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `venv/bin/pytest tests/meeting/test_role_classify.py -v`
Expected: FAIL (`classify_role` not defined).

- [ ] **Step 3: Implement**

In `meeting/services/role_mapping.py`, add near the top (after the existing `import re`):

```python
import json

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
```

Append at the end of the module:

```python
_CLASSIFY_SYSTEM = """\
Bạn phân loại CHỨC DANH (jobTitle) của một nhân sự vào ĐÚNG MỘT vai trò trong
danh sách CỐ ĐỊNH dưới đây. CHỈ được chọn tên vai trò có sẵn — KHÔNG bịa tên mới.

Các vai trò (name — mô tả — data_plan):
{catalog}

Chức danh cần phân loại: "{job_title}"

Trả về DUY NHẤT một JSON: {{"role": "<tên vai trò chính xác trong danh sách>",
"confidence": <0..1>}}. Nếu không vai trò nào hợp lý, trả về đúng chữ: NONE
"""


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def classify_role(job_title, roles, *, generate, threshold: float = 0.6) -> str | None:
    """LLM fallback: map an unmatched jobTitle to the best-fit EXISTING role name.

    `generate(messages) -> str` is injected. Returns a pool role name only when the
    model is confident AND the name is in the pool; otherwise None (never invents).
    """
    if not job_title or not roles:
        return None
    pool = {r.name for r in roles}
    catalog = "\n".join(
        f"- {r.name} — {(r.description or '').strip()} — {r.data_plan}" for r in roles
    )
    content = _CLASSIFY_SYSTEM.format(catalog=catalog, job_title=job_title)
    try:
        raw = _strip_think(generate([{"role": "system", "content": content}]))
    except Exception:
        return None
    if not raw or raw.strip().upper() == "NONE":
        return None
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    name = obj.get("role")
    conf = obj.get("confidence")
    if name not in pool:
        return None
    try:
        if float(conf) < threshold:
            return None
    except (TypeError, ValueError):
        return None
    return name
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/pytest tests/meeting/test_role_classify.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add meeting/services/role_mapping.py tests/meeting/test_role_classify.py
git commit -m "feat(chat): classify_role LLM fallback (closed-set, confidence-guarded)"
```

---

## Task 3: `add_role_alias` repo helper (dedup write-back)

**Files:** `meeting/db/repositories.py`, `tests/meeting/test_roles_repo.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/meeting/test_roles_repo.py`:

```python
class _ExecRecordingSession:
    """Captures the SQL text + params passed to execute()."""
    def __init__(self):
        self.calls = []
    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return _FakeResult([])


async def test_add_role_alias_issues_dedup_update():
    import uuid as _uuid
    session = _ExecRecordingSession()
    rid = _uuid.uuid4()
    await repo.add_role_alias(session, rid, "Applied AI Intern")
    sql, params = session.calls[0]
    assert "array_append" in sql
    assert "ANY(aliases)" in sql
    assert params["alias"] == "Applied AI Intern"
    assert params["role_id"] == rid
```

- [ ] **Step 2: Run to verify fail**

Run: `venv/bin/pytest tests/meeting/test_roles_repo.py::test_add_role_alias_issues_dedup_update -v`
Expected: FAIL (`add_role_alias` missing).

- [ ] **Step 3: Implement**

In `meeting/db/repositories.py`, ensure `from sqlalchemy import text` is imported (add if missing), then add:

```python
async def add_role_alias(session: AsyncSession, role_id, alias: str) -> None:
    """Append `alias` to a role's aliases array, skipping if already present.

    Dedup via `NOT (:alias = ANY(aliases))`. Single-instance cron → no locking.
    """
    stmt = text(
        "UPDATE roles SET aliases = array_append(aliases, :alias) "
        "WHERE id = :role_id AND NOT (:alias = ANY(aliases))"
    )
    await session.execute(stmt, {"alias": alias, "role_id": role_id})
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/pytest tests/meeting/test_roles_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add meeting/db/repositories.py tests/meeting/test_roles_repo.py
git commit -m "feat(db): add_role_alias dedup write-back helper"
```

---

## Task 4: worker script `scripts/classify_roles.py`

**Files:** `scripts/classify_roles.py` (create)

Read `scripts/sync_memory.py` FIRST and mirror its scaffolding: how it loads env, opens an async session (`AsyncSessionLocal` from `meeting.db.base`), builds the LLM client, parses args, and logs. Reuse `_llm_client`/`_llm_model` from `meeting.graphs._chat_llm` (as `meeting/api/chat.py` does). If those import paths differ from the real files, adapt to match and NOTE it.

- [ ] **Step 1: Write the script**

`scripts/classify_roles.py`:

```python
"""Background worker: classify unmatched users' jobTitles into the role pool.

For each user with a `position` but no `role_id`:
  1. deterministic resolve_role(position) first (cheap; a freshly-added alias may match)
  2. miss → classify_role(position) via LLM
  3. confident hit → append position to that role's aliases (self-heal) + backfill role_id

Idempotent + re-runnable. Best-effort per user (one failure is logged, never sinks
the batch). Run: venv/bin/python scripts/classify_roles.py [--dry-run]

See docs/superpowers/specs/2026-06-15-role-autoclassify-design.md.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from meeting.db.base import AsyncSessionLocal
from meeting.db import models, repositories as repo
from meeting.services.role_mapping import classify_role, resolve_role
from meeting.graphs._chat_llm import _llm_client, _llm_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("classify_roles")


def _make_generate():
    client = _llm_client()
    model = _llm_model()

    def generate(messages):
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=120
        )
        return resp.choices[0].message.content or ""

    return generate


async def run(dry_run: bool = False) -> dict:
    generate = _make_generate()
    stats = {"scanned": 0, "matched": 0, "classified": 0, "skipped": 0}
    async with AsyncSessionLocal() as session:
        roles = await repo.list_roles(session)
        by_name = {r.name: r for r in roles}
        users = (
            await session.execute(
                select(models.User).where(
                    models.User.position.is_not(None),
                    models.User.role_id.is_(None),
                )
            )
        ).scalars().all()
        for user in users:
            stats["scanned"] += 1
            try:
                name = resolve_role(user.position, roles)
                if name:
                    stats["matched"] += 1
                else:
                    name = classify_role(user.position, roles, generate=generate)
                    if name:
                        stats["classified"] += 1
                if not name:
                    stats["skipped"] += 1
                    logger.info("skip user=%s position=%r (no confident match)", user.id, user.position)
                    continue
                role = by_name.get(name)
                if role is None:
                    stats["skipped"] += 1
                    continue
                if dry_run:
                    logger.info("[dry-run] user=%s %r -> %s", user.id, user.position, name)
                    continue
                # self-heal: remember this title for next time (deterministic match)
                await repo.add_role_alias(session, role.id, user.position)
                user.role_id = role.id
                await session.commit()
                logger.info("user=%s %r -> %s", user.id, user.position, name)
            except Exception as e:  # one bad user must not sink the batch
                await session.rollback()
                stats["skipped"] += 1
                logger.warning("classify failed user=%s: %s", user.id, e)
    logger.info("done: %s", stats)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Classify unmatched users into the role pool.")
    ap.add_argument("--dry-run", action="store_true", help="log decisions, write nothing")
    args = ap.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
```

- [ ] **Step 2: Smoke-import (no DB write)**

Run: `DATABASE_URL=postgresql://u:p@localhost:5432/db venv/bin/python -c "import scripts.classify_roles"` (importing `meeting.db.base` requires `DATABASE_URL`).
Expected: no import error. (A full run needs a live DB + LLM; defer to manual/cron.)

- [ ] **Step 3: Commit**

```bash
git add scripts/classify_roles.py
git commit -m "feat(scripts): background role auto-classify worker"
```

---

## Task 5: Verification

- [ ] **Step 1: Full suite**

Run: `venv/bin/pytest tests/meeting -q`
Expected: all green, no NEW failures vs baseline (Feature 1 left the suite fully green at 303).

- [ ] **Step 2: Apply migration (user-run, when ready)**

```bash
venv/bin/alembic upgrade head     # applies 0021 (users.position)
```

- [ ] **Step 3: First run**

```bash
venv/bin/python scripts/classify_roles.py --dry-run   # inspect decisions
venv/bin/python scripts/classify_roles.py             # write
```

---

## Self-Review notes

- **Spec coverage:** A (users.position) → Task 1; B (classify_role) → Task 2; C (add_role_alias + backfill) → Task 3 + worker; D (worker) → Task 4; tests → each task + Task 5.
- **DRY:** `_strip_think` is re-defined locally in `role_mapping.py` (small, avoids importing the private one from `kickoff.py`); acceptable. If preferred, promote one shared copy to a util — optional, not required.
- **Idempotency:** migration `0021` guarded; `add_role_alias` deduped; worker re-runnable.
- **Confidence threshold** (0.6) is a constant in `classify_role`; tune later if needed.
- **Out of scope:** new role rows, review queue, Feature 2 (all per spec).
