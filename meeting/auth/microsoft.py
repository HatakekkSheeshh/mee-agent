"""MicrosoftProvider — real O365 OAuth via MSAL.

STUB FOR NOW. When IT grants Azure AD app registration:
  1. pip install msal
  2. Set MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID in .env
  3. Implement the two methods below using msal.ConfidentialClientApplication
  4. Switch AUTH_PROVIDER=microsoft in .env

No other code changes needed — callback, session, voice enrollment all
work because they consume the same UserInfo dataclass.

Reference (MSAL Python docs):
  https://learn.microsoft.com/en-us/entra/msal/python/
  https://github.com/Azure-Samples/ms-identity-python-webapp
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

from meeting.auth.base import UserInfo


class MicrosoftProvider:
    name = "microsoft"
    requires_csrf_state = True  # CSRF protection mandatory for real OAuth

    def __init__(self) -> None:
        self.client_id = os.environ.get("MS_CLIENT_ID", "")
        self.client_secret = os.environ.get("MS_CLIENT_SECRET", "")
        self.tenant_id = os.environ.get("MS_TENANT_ID", "common")
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "MicrosoftProvider needs MS_CLIENT_ID + MS_CLIENT_SECRET env vars. "
                "Use AUTH_PROVIDER=mock for dev until IT grants Azure AD app."
            )

    def get_login_url(self, state: str, redirect_uri: str) -> str:
        # Standard Authorization Code flow — when implementing for real,
        # replace this hand-built URL with msal.ConfidentialClientApplication
        # .get_authorization_request_url(scopes, state, redirect_uri).
        params = urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid email profile User.Read offline_access",
            "state": state,
            "response_mode": "query",
        })
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?{params}"

    def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        # When implementing:
        #   1. msal_app.acquire_token_by_authorization_code(code, scopes, redirect_uri)
        #   2. Decode id_token (validate signature against JWKS at
        #      https://login.microsoftonline.com/{tenant}/discovery/keys)
        #   3. Extract claims: email/preferred_username, name, oid, tid
        #   4. (Optional) Call GET https://graph.microsoft.com/v1.0/me/photo/$value
        #      for avatar_url
        #   5. Return UserInfo(email=..., display_name=..., ms_oid=oid,
        #                      ms_tenant_id=tid, avatar_url=...)
        raise NotImplementedError(
            "MicrosoftProvider not implemented yet. Use AUTH_PROVIDER=mock. "
            "When ready, install msal and complete this method following the "
            "reference docs at the top of this file."
        )
