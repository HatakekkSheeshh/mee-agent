"""Role seed data — the 10 company roles that populate the `roles` pool.

Lives in src/db/seed_roles.py so both the Alembic migration and this test
share one source of truth. Locks count, uniqueness, well-formedness, and the
data_plan assignments agreed in the design spec.
"""
from __future__ import annotations

from src.db.seed_roles import SEED_ROLES

VALID_PLANS = {"own_tasks", "cross_project", "minimal"}


def test_seed_has_ten_roles():
    assert len(SEED_ROLES) == 10


def test_seed_names_unique():
    names = [r["name"] for r in SEED_ROLES]
    assert len(set(names)) == len(names)


def test_seed_every_role_well_formed():
    for r in SEED_ROLES:
        assert r["name"], r
        assert r["description"], r
        assert r["data_plan"] in VALID_PLANS, r
        assert r["kickoff_prompt"], r


def test_every_seed_role_has_aliases_list():
    for r in SEED_ROLES:
        assert "aliases" in r
        assert isinstance(r["aliases"], list)


def test_seed_data_plan_assignments_match_spec():
    plan = {r["name"]: r["data_plan"] for r in SEED_ROLES}
    assert plan["AI Applied"] == "own_tasks"
    assert plan["AI Engineer"] == "own_tasks"
    assert plan["Software Engineer"] == "own_tasks"
    assert plan["Associate System Manager"] == "own_tasks"
    assert plan["Lead System Engineer"] == "cross_project"
    assert plan["Business Analyst"] == "cross_project"
    assert plan["Lead QC Engineer"] == "cross_project"
    assert plan["Lead Software Engineer"] == "cross_project"
    assert plan["Associate Product Growth Executive"] == "cross_project"
    assert plan["L&D Executive"] == "minimal"
