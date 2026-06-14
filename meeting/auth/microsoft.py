"""MicrosoftProvider — real O365 OAuth via MSAL.

Authorization Code flow against Azure AD:
  1. get_login_url() → redirect user to login.microsoftonline.com
  2. Microsoft redirects back to /auth/callback?code=...&state=...
  3. exchange_code() trades the code for tokens via MSAL, reads the verified
     id_token_claims to build a UserInfo (email + oid + tid), AND serializes the
     MSAL token cache (which now holds the refresh token) so the callback can
     persist it encrypted — that refresh token lets us mint Microsoft Graph
     access tokens later (see meeting.auth.tokens) to call pm-agent's JWT path.

Config (env): MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID. redirect_uri is
supplied by routes (pinned to MS_REDIRECT_URI). Switch on with
AUTH_PROVIDER=microsoft.

Scope is User.Read — MSAL implicitly adds openid/profile/offline_access, so the
cache gets a refresh token and the access token is a Graph token (what
pm-agent validates via Graph /me).
"""
from __future__ import annotations

import os
from typing import Optional

from meeting.auth.base import UserInfo

# MSAL implicitly requests openid/profile/offline_access; User.Read gets us a
# Graph token (validated by pm-agent's /me call) + a refresh token in the cache.
SCOPES = ["User.Read"]


def _authority(tenant_id: Optional[str] = None) -> str:
    tid = tenant_id or os.environ.get("MS_TENANT_ID", "common")
    return f"https://login.microsoftonline.com/{tid}"


def build_msal_app(cache=None):
    """Build a ConfidentialClientApplication bound to an (optional) token cache.

    Used both at login (exchange_code) and later for silent refresh
    (meeting.auth.tokens) so the same client_id/authority/secret config drives
    both. Lazy msal import keeps module import cheap.
    """
    import msal

    return msal.ConfidentialClientApplication(
        os.environ["MS_CLIENT_ID"],
        authority=_authority(),
        client_credential=os.environ["MS_CLIENT_SECRET"],
        token_cache=cache,
    )


class MicrosoftProvider:
    name = "microsoft"
    requires_csrf_state = True  # CSRF protection mandatory for real OAuth

    def __init__(self, *, msal_app=None) -> None:
        self.client_id = os.environ.get("MS_CLIENT_ID", "")
        self.client_secret = os.environ.get("MS_CLIENT_SECRET", "")
        self.tenant_id = os.environ.get("MS_TENANT_ID", "common")
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "MicrosoftProvider needs MS_CLIENT_ID + MS_CLIENT_SECRET env vars. "
                "Use AUTH_PROVIDER=mock for dev until IT grants Azure AD app."
            )
        # Injected in tests; in prod each call builds an app bound to its cache.
        self._injected_app = msal_app

    def _app(self, cache=None):
        return self._injected_app if self._injected_app is not None else build_msal_app(cache)

    def get_login_url(self, state: str, redirect_uri: str) -> str:
        return self._app().get_authorization_request_url(
            SCOPES, state=state, redirect_uri=redirect_uri
        )

    def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        import msal

        cache = msal.SerializableTokenCache()
        result = self._app(cache).acquire_token_by_authorization_code(
            code, scopes=SCOPES, redirect_uri=redirect_uri
        )
        if not isinstance(result, dict) or "error" in result:
            detail = ""
            if isinstance(result, dict):
                detail = result.get("error_description") or result.get("error") or ""
            raise ValueError(f"token exchange failed: {detail}".strip())

        claims = result.get("id_token_claims") or {}
        email = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        ).strip().lower()
        if not email:
            raise ValueError("id_token missing email/preferred_username claim")

        oid: Optional[str] = claims.get("oid")
        tid: Optional[str] = claims.get("tid")
        name = (claims.get("name") or "").strip() or email.split("@")[0]
        # Serialize the cache (now holding the refresh token) so the callback
        # can persist it encrypted. Empty string when nothing was cached
        # (e.g. injected test app that doesn't populate the cache).
        token_cache = cache.serialize() if cache.serialize() != "{}" else None
        return UserInfo(
            email=email,
            display_name=name,
            ms_oid=oid,
            ms_tenant_id=tid,
            ms_token_cache=token_cache,
        )
