"""Auth package — provider-agnostic OAuth + session management.

Swap providers via env var `AUTH_PROVIDER`:
  - mock       → MockProvider (dev/demo, full mimic O365 UI)
  - microsoft  → MicrosoftProvider (real O365 via MSAL — when IT grants)

Downstream code (callback handler, session, user creation, voice enrollment)
is provider-agnostic — only the login URL + code exchange differ.
"""
from src.auth.base import AuthProvider, UserInfo  # noqa: F401
from src.auth.session import get_current_user, get_current_user_optional  # noqa: F401
from src.auth.routes import router as auth_router  # noqa: F401
