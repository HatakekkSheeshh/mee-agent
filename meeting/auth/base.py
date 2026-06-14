"""Provider-agnostic auth contract.

Each provider implements the same 2 methods. The /auth/login + /auth/callback
endpoints call these via the selected provider so the FastAPI routes stay
identical between mock + real O365.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class UserInfo:
    """Normalized user info returned by a provider after code exchange.

    Maps cleanly onto the `users` table columns. `ms_oid` and `ms_tenant_id`
    are Microsoft-specific (None when MockProvider creates the user, real
    values when MicrosoftProvider decodes the id_token).
    """
    email: str
    display_name: str
    avatar_url: Optional[str] = None
    ms_oid: Optional[str] = None
    ms_tenant_id: Optional[str] = None
    # Serialized MSAL token cache (plaintext JSON) captured at code-exchange.
    # Holds the refresh token used to mint Graph access tokens later. The
    # callback encrypts this before persisting to users.refresh_token. None for
    # MockProvider (no real Microsoft tokens).
    ms_token_cache: Optional[str] = None


class AuthProvider(Protocol):
    """Each concrete provider returns the same shape from these two calls.

    `get_login_url(state)` is what the FE redirects the user to so the auth
    flow starts; `exchange_code(code)` is what /auth/callback uses to turn
    the returned authorization code into normalized user info we can persist.
    """

    name: str  # "mock" | "microsoft" — for logging + diagnostic
    requires_csrf_state: bool  # True for real O365 (CSRF protection)

    def get_login_url(self, state: str, redirect_uri: str) -> str:
        """URL the FE redirects the user to. State is a CSRF-protection nonce
        that the provider echoes back to /auth/callback so we can validate
        the response wasn't injected.
        """
        ...

    def exchange_code(self, code: str, redirect_uri: str) -> UserInfo:
        """Validate the authorization code and return user info. Raises
        ValueError on invalid code, expired session, or signature mismatch.
        """
        ...
