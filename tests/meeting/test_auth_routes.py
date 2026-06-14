"""Tests for auth route helpers — redirect_uri pinning.

The dev frontend (Vite :8001) proxies /auth → backend :8002 with
changeOrigin=true, which rewrites the host header to :8002. So deriving the
OAuth redirect_uri from the request would produce :8002 and fail the exact-match
Azure registered :8001 URL. MS_REDIRECT_URI pins it to the registered value.
"""
from __future__ import annotations

from types import SimpleNamespace

from meeting.auth.routes import _redirect_uri


def _fake_request(scheme="http", netloc="localhost:8002", headers=None):
    return SimpleNamespace(
        url=SimpleNamespace(scheme=scheme, netloc=netloc),
        headers=headers or {},
    )


def test_redirect_uri_uses_env_override_when_set(monkeypatch):
    monkeypatch.setenv("MS_REDIRECT_URI", "http://localhost:8001/auth/callback")
    # Request looks like it came through the proxy as :8002 — must be ignored.
    assert _redirect_uri(_fake_request(netloc="localhost:8002")) == "http://localhost:8001/auth/callback"


def test_redirect_uri_derives_from_request_without_override(monkeypatch):
    monkeypatch.delenv("MS_REDIRECT_URI", raising=False)
    assert _redirect_uri(_fake_request(scheme="https", netloc="app.example")) == "https://app.example/auth/callback"


# ─── voice-enrollment gate (VOICE_ENROLL_OPTIONAL) ──────────────────

from meeting.auth.routes import _enrollment_satisfied


def test_enrollment_gate_without_flag(monkeypatch):
    monkeypatch.delenv("VOICE_ENROLL_OPTIONAL", raising=False)
    assert _enrollment_satisfied(SimpleNamespace(voice_enrolled=True)) is True
    assert _enrollment_satisfied(SimpleNamespace(voice_enrolled=False)) is False


def test_enrollment_gate_flag_treats_everyone_as_enrolled(monkeypatch):
    # Deploys without the local pyannote/torch stack set this so login lands on
    # /app instead of the (unavailable) enrollment flow.
    monkeypatch.setenv("VOICE_ENROLL_OPTIONAL", "true")
    assert _enrollment_satisfied(SimpleNamespace(voice_enrolled=False)) is True
