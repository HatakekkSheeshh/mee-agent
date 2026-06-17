"""
Shared test setup for the meeting/ package suite.

Importing meeting.services / meeting.graphs transitively imports
meeting.db.base, which requires DATABASE_URL at import time (it builds a
SQLAlchemy async engine — lazily, so no connection is made). We seed dummy
env here, before any test module is collected, so unit tests can import the
code under test without a live database or real secrets.
"""
import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@localhost:5432/test_meeting"
)
os.environ.setdefault("PM_AGENT_A2A_URL", "https://pm-agent.example/a2a/")
os.environ.setdefault("PM_AGENT_API_KEY", "test-key")
