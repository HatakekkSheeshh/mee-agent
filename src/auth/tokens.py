"""Per-user Microsoft Graph access tokens for pm-agent's JWT auth path.

pm-agent validates the bearer by calling Graph /me, so it needs a live Graph
access token — not the user's OID. Graph access tokens live ~1h, so we keep the
long-lived refresh token (in the encrypted MSAL cache on users.refresh_token)
and mint fresh access tokens on demand via MSAL silent refresh.

`get_graph_access_token(user, session)`:
  1. Serve a still-valid token from the in-process cache (avoids a refresh per
     chat message).
  2. Otherwise decrypt the user's MSAL cache and acquire_token_silent (Azure
     rotates the refresh token; persist the rotated cache back).
  3. No usable refresh token → ReauthRequired (caller turns this into a 401 so
     the user signs in again, which repopulates the cache).

The in-memory cache is process-local and lost on restart — that's fine, it's a
latency optimization; the durable state is the encrypted cache in the DB.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from meeting.auth.microsoft import SCOPES, build_msal_app
from meeting.auth.token_crypto import decrypt_token, encrypt_token
from meeting.db.models import User

logger = logging.getLogger(__name__)

# Refresh this many seconds before the real expiry so an in-flight pm-agent call
# never races the token going stale.
_EXPIRY_SKEW = 120

# user_id (str) → (access_token, expiry_epoch_seconds)
_cache: Dict[str, Tuple[str, float]] = {}


class ReauthRequired(Exception):
    """The stored refresh token is missing or no longer valid — the user must
    sign in again to repopulate it."""


def _acquire_silent_sync(cache_blob: str) -> Tuple[Optional[dict], Optional[str]]:
    """Blocking MSAL silent refresh. Returns (token_result, rotated_cache_blob).

    rotated_cache_blob is the re-serialized cache when MSAL rotated the refresh
    token (else None). Runs under asyncio.to_thread — MSAL is synchronous.
    """
    import msal

    cache = msal.SerializableTokenCache()
    if cache_blob:
        cache.deserialize(cache_blob)
    app = build_msal_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        return None, None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    rotated = cache.serialize() if cache.has_state_changed else None
    return result, rotated


async def get_graph_access_token(user: User, session: AsyncSession) -> str:
    """Return a valid Graph access token for `user`, refreshing if needed.

    Raises ReauthRequired when there's no usable refresh token.
    """
    uid = str(user.id)
    now = time.time()

    hit = _cache.get(uid)
    if hit and hit[1] - _EXPIRY_SKEW > now:
        return hit[0]

    cache_blob = decrypt_token(user.refresh_token) if user.refresh_token else None
    if not cache_blob:
        raise ReauthRequired("no stored Microsoft token for user")

    result, rotated = await asyncio.to_thread(_acquire_silent_sync, cache_blob)
    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description") or "silent refresh failed"
        logger.warning("[tokens] reauth required for user=%s: %s", uid, detail)
        raise ReauthRequired(detail)

    token = result["access_token"]
    _cache[uid] = (token, now + int(result.get("expires_in", 3600)))

    if rotated:
        # Azure rotated the refresh token — persist the new cache so the next
        # process / cache-miss can still refresh.
        user.refresh_token = encrypt_token(rotated)
        await session.flush()

    return token
