"""AES-256-GCM encryption for OAuth refresh tokens stored at rest.

The Microsoft refresh token (inside the serialized MSAL cache) is long-lived
and sensitive — anyone holding it can mint Graph access tokens as the user. We
encrypt it before writing to `users.refresh_token` so a DB leak alone doesn't
expose it.

Key: derived (SHA-256 → 32 bytes) from TOKEN_ENC_KEY (falls back to
SESSION_SECRET). Deriving means any-length env string yields a valid AES-256
key. Ciphertext is `v1:` + base64(nonce ‖ ciphertext+tag) — the prefix tags the
scheme so we can rotate later. decrypt_token never raises (bad/old/garbage
input → None) so callers treat "can't decrypt" the same as "no token".
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_PREFIX = "v1:"
_NONCE_LEN = 12  # AES-GCM standard nonce size


def _key() -> bytes:
    secret = os.environ.get("TOKEN_ENC_KEY") or os.environ.get("SESSION_SECRET") or ""
    if not secret:
        raise RuntimeError(
            "TOKEN_ENC_KEY (or SESSION_SECRET) must be set to encrypt refresh tokens."
        )
    return hashlib.sha256(secret.encode()).digest()  # 32 bytes → AES-256


def encrypt_token(plaintext: str) -> str:
    """Encrypt → `v1:<base64(nonce+ct)>`. Random nonce ⇒ non-deterministic."""
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_token(stored: Optional[str]) -> Optional[str]:
    """Inverse of encrypt_token. Returns None for anything we can't decrypt
    (wrong scheme, wrong key, corrupted) — never raises."""
    if not stored or not stored.startswith(_PREFIX):
        return None
    try:
        raw = base64.urlsafe_b64decode(stored[len(_PREFIX):])
        nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
        return AESGCM(_key()).decrypt(nonce, ct, None).decode()
    except Exception as e:  # InvalidTag, padding, decode, etc.
        logger.warning("decrypt_token failed: %s", type(e).__name__)
        return None
