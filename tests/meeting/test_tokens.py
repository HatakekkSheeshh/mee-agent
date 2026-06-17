"""Tests for src.auth.tokens.get_graph_access_token.

The MSAL silent-refresh round-trip (_acquire_silent_sync) is the one piece that
does network I/O, so it's monkeypatched. Everything else — in-memory caching,
expiry skew, rotation persistence, ReauthRequired — is the real logic under test.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.auth import tokens, token_crypto


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setenv("TOKEN_ENC_KEY", "0123456789abcdef0123456789abcdef")
    tokens._cache.clear()
    yield
    tokens._cache.clear()


class _FakeSession:
    def __init__(self):
        self.flushed = False

    async def flush(self):
        self.flushed = True


def _user(refresh_token=None):
    return SimpleNamespace(id=uuid.uuid4(), refresh_token=refresh_token)


async def test_returns_access_token_and_caches(monkeypatch):
    user = _user(refresh_token=token_crypto.encrypt_token("cache-blob"))
    calls = {"n": 0}

    def fake_acquire(blob):
        calls["n"] += 1
        return {"access_token": "graph-jwt-aaa", "expires_in": 3600}, None

    monkeypatch.setattr(tokens, "_acquire_silent_sync", fake_acquire)
    session = _FakeSession()

    tok = await tokens.get_graph_access_token(user, session)
    assert tok == "graph-jwt-aaa"

    # Second call within expiry → served from cache, no second acquire.
    tok2 = await tokens.get_graph_access_token(user, session)
    assert tok2 == "graph-jwt-aaa"
    assert calls["n"] == 1


async def test_no_refresh_token_raises_reauth(monkeypatch):
    user = _user(refresh_token=None)
    with pytest.raises(tokens.ReauthRequired):
        await tokens.get_graph_access_token(user, _FakeSession())


async def test_acquire_returns_none_raises_reauth(monkeypatch):
    user = _user(refresh_token=token_crypto.encrypt_token("cache-blob"))
    monkeypatch.setattr(tokens, "_acquire_silent_sync", lambda blob: (None, None))
    with pytest.raises(tokens.ReauthRequired):
        await tokens.get_graph_access_token(user, _FakeSession())


async def test_rotated_cache_is_persisted(monkeypatch):
    user = _user(refresh_token=token_crypto.encrypt_token("old-blob"))

    def fake_acquire(blob):
        return {"access_token": "graph-jwt-bbb", "expires_in": 3600}, "new-rotated-blob"

    monkeypatch.setattr(tokens, "_acquire_silent_sync", fake_acquire)
    session = _FakeSession()

    await tokens.get_graph_access_token(user, session)

    assert session.flushed is True
    # The new blob was re-encrypted and stored (decrypts back to the new value).
    assert token_crypto.decrypt_token(user.refresh_token) == "new-rotated-blob"
