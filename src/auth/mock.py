"""MockProvider — for local dev + demo while real O365 isn't granted.

Flow:
  1. /auth/login redirects user to /auth/mock-login?state=...
  2. Mock login page (looks like Microsoft) → user types email + name
  3. Mock page POSTs back to /auth/callback?code=mock:<base64-of-userinfo>&state=...
  4. exchange_code() decodes the base64 payload → UserInfo

The `mock:` prefix lets the callback route identify mock codes without
extra config. Real O365 codes are random opaque strings from Microsoft.

Trade-off: no real signature verification. Anyone who can hit /auth/callback
with a crafted ?code=mock:... can impersonate any email. Acceptable for dev
mode behind the AUTH_PROVIDER=mock env var; never enable on production.
"""
from __future__ import annotations

import base64
import json
from typing import Optional
from urllib.parse import urlencode

from meeting.auth.base import UserInfo


MOCK_PREFIX = "mock:"


def encode_mock_code(info: UserInfo) -> str:
    """Pack a UserInfo into a mock authorization code. Used by the
    mock-login page when posting back to /auth/callback.
    """
    payload = {
        "email": info.email,
        "display_name": info.display_name,
        "avatar_url": info.avatar_url,
    }
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return MOCK_PREFIX + b64


def _decode_mock_code(code: str) -> dict:
    if not code.startswith(MOCK_PREFIX):
        raise ValueError("not a mock code")
    body = code[len(MOCK_PREFIX):]
    # Restore padding stripped by urlsafe_b64encode above.
    padded = body + "=" * (-len(body) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception as e:
        raise ValueError(f"invalid mock code payload: {e}") from e


class MockProvider:
    name = "mock"
    requires_csrf_state = False  # mock is dev-only, skip CSRF for simpler demo

    def get_login_url(self, state: str, redirect_uri: str) -> str:
        # Point FE at the mock login page. It collects email + name + posts
        # to /auth/callback with a mock code.
        params = urlencode({"state": state, "redirect_uri": redirect_uri})
        return f"/auth/mock-login?{params}"

    def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        payload = _decode_mock_code(code)
        email = (payload.get("email") or "").strip().lower()
        name = (payload.get("display_name") or "").strip()
        if not email:
            raise ValueError("mock code missing email")
        return UserInfo(
            email=email,
            display_name=name or email.split("@")[0],
            avatar_url=payload.get("avatar_url"),
            ms_oid=None,           # mock has no real Microsoft Object ID
            ms_tenant_id=None,
        )
