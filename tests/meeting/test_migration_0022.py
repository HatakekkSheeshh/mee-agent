"""0022 — ensure chat_sessions.meeting_id is nullable (user-scoped sessions).

The shared DB is unavailable offline, so this verifies the migration's identity
(single-head chain onto 0021) and that the ORM model declares meeting_id
nullable — the invariant the migration guarantees on the drifted prod DB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from meeting.db.models import ChatSession

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0022_chat_sessions_meeting_nullable.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0022", _MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_0022_chains_onto_0021():
    mod = _load_migration()
    assert mod.revision == "0022"
    assert mod.down_revision == "0021"


def test_chat_session_meeting_id_is_nullable():
    # The model is the source of truth the migration enforces in the DB.
    assert ChatSession.__table__.c.meeting_id.nullable is True
