"""MicrosoftProvider — real O365 OAuth via MSAL.

Authorization Code flow against Azure AD:
  1. get_login_url() → redirect user to login.microsoftonline.com
  2. Microsoft redirects back to /auth/callback?code=...&state=...
  3. exchange_code() trades the code for tokens via MSAL, then reads the
     verified id_token_claims to build a UserInfo (email + oid + tid).

Config (env): MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID. The redirect_uri
is supplied by the routes layer (pinned to MS_REDIRECT_URI so it matches the
URL registered in Azure regardless of the dev proxy host). Switch on with
AUTH_PROVIDER=microsoft.

Scope is User.Read — MSAL implicitly adds openid/profile/offline_access. That
yields a Microsoft Graph access token, which is what pm-agent's JWT path
validates via Graph /me when we move off the direct-oid path later.

The callback, session, upsert, and voice-enrollment routes are unchanged: they
consume the same UserInfo dataclass the mock provider returns.
"""
from __future__ import annotations

import os
from typing import Optional

from meeting.auth.base import UserInfo

# MSAL implicitly requests openid/profile/offline_access; User.Read gets us a
# Graph token (needed for the future JWT-forwarding step).
_SCOPES = ["User.Read"]


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
        # Injected in tests; built lazily in prod so importing this module never
        # forces an MSAL/network dependency until a login actually happens.
        self._app = msal_app

    def _get_app(self):
        if self._app is None:
            import msal

            self._app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                client_credential=self.client_secret,
            )
        return self._app

    def get_login_url(self, state: str, redirect_uri: str) -> str:
        return self._get_app().get_authorization_request_url(
            _SCOPES, state=state, redirect_uri=redirect_uri
        )

    def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        result = self._get_app().acquire_token_by_authorization_code(
            code, scopes=_SCOPES, redirect_uri=redirect_uri
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
        return UserInfo(
            email=email,
            display_name=name,
            ms_oid=oid,
            ms_tenant_id=tid,
        )
