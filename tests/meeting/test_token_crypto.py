"""Tests for token_crypto — AES-256-GCM encryption of refresh tokens at rest."""
from __future__ import annotations

import pytest

from src.auth import token_crypto


@pytest.fixture(autouse=True)
def _enc_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENC_KEY", "0123456789abcdef0123456789abcdef")


def test_roundtrip_encrypt_decrypt():
    secret = "1//0gFAKE-refresh-token-cache-blob-äöü"
    enc = token_crypto.encrypt_token(secret)
    assert enc != secret               # actually encrypted
    assert token_crypto.decrypt_token(enc) == secret


def test_ciphertext_is_nondeterministic():
    # Random nonce per call → same plaintext encrypts to different ciphertext.
    a = token_crypto.encrypt_token("same")
    b = token_crypto.encrypt_token("same")
    assert a != b
    assert token_crypto.decrypt_token(a) == token_crypto.decrypt_token(b) == "same"


def test_decrypt_garbage_returns_none():
    assert token_crypto.decrypt_token("not-our-format") is None
    assert token_crypto.decrypt_token("") is None
    assert token_crypto.decrypt_token(None) is None  # type: ignore[arg-type]


def test_decrypt_with_wrong_key_returns_none(monkeypatch):
    enc = token_crypto.encrypt_token("secret")
    monkeypatch.setenv("TOKEN_ENC_KEY", "DIFFERENT-key-DIFFERENT-key-DIFF")
    # GCM auth tag fails under a different key → None, never raises.
    assert token_crypto.decrypt_token(enc) is None
