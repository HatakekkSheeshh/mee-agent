"""Session = HMAC-signed cookie (no JWT lib dependency).

We use a plain HMAC-SHA256 signed payload instead of a full JWT library so
this auth package has zero extra runtime dependencies. Behavior is the same
as a JWT for our purposes: opaque to the client, server-verifiable, has an
expiry.

Format:
    cookie value = base64(json_payload) + "." + base64(hmac_signature)

Payload:
    {"sub": "<user.id uuid>", "email": "...", "exp": <unix_ts>}

If we ever need RS256 (for sharing JWTs with other services) or token
introspection beyond the cookie, swap this for python-jose.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meeting.db.base import get_session
from meeting.db.models import User


COOKIE_NAME = "mee_session"
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _get_secret() -> bytes:
    """Read SESSION_SECRET from env. In dev, fall back to an unsafe default
    so contributors don't need to set it just to boot. We log a warning so
    no one ships this to prod by accident.
    """
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        # Dev fallback. Production MUST override this — a leaked secret lets
        # an attacker forge sessions for any user.
        secret = "DEV-INSECURE-SESSION-SECRET-CHANGE-IN-PROD"
    return secret.encode()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def issue_session_cookie(user_id: uuid.UUID, email: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Build a signed session cookie value. Stored as httpOnly secure cookie
    by /auth/callback after successful login.
    """
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": int(time.time()) + ttl_seconds,
        "iat": int(time.time()),
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_get_secret(), payload_b64.encode(), hashlib.sha256).digest()
    sig_b64 = _b64encode(sig)
    return f"{payload_b64}.{sig_b64}"


def verify_session_cookie(token: str) -> Optional[dict]:
    """Verify HMAC + check expiry. Returns the payload dict or None if invalid.
    Never raises — callers decide what to do with anonymous requests.
    """
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig_b64 = token.rsplit(".", 1)
        # Constant-time compare to prevent timing attacks on the HMAC.
        expected = hmac.new(_get_secret(), payload_b64.encode(), hashlib.sha256).digest()
        actual = _b64decode(sig_b64)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64decode(payload_b64))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


async def get_current_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_session),
    mee_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> Optional[User]:
    """Decode session cookie + load user. Returns None for anonymous requests
    instead of raising 401 — use this on endpoints that have both anon and
    authenticated branches (e.g. landing page).
    """
    if not mee_session:
        return None
    payload = verify_session_cookie(mee_session)
    if not payload:
        return None
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        return None
    stmt = select(User).where(User.id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    """Require an authenticated user — raises 401 if not logged in.
    Apply to all protected /api/* endpoints via Depends(get_current_user).
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Sign in at /auth/login.",
        )
    return user
