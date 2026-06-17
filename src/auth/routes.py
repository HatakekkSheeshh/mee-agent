"""Auth HTTP endpoints — provider-agnostic.

Routes:
  GET  /auth/login         — start the OAuth flow (redirect to provider)
  GET  /auth/mock-login    — mock provider's fake-MS login page (HTML)
  POST /auth/mock-submit   — mock provider's form post → redirect to /auth/callback
  GET  /auth/callback      — provider redirects back here with ?code=...&state=...
  POST /auth/logout        — clear the session cookie
  GET  /auth/me            — current user info (frontend uses to gate UI)

The provider (mock vs microsoft) is chosen once at import time from
AUTH_PROVIDER env var. Switching providers is a single env-var change +
restart — no code edits.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.base import AuthProvider
from src.auth.mock import MockProvider, encode_mock_code
from src.auth.base import UserInfo
from src.auth.token_crypto import encrypt_token
from src.auth.session import (
    COOKIE_NAME,
    DEFAULT_TTL_SECONDS,
    get_current_user_optional,
    issue_session_cookie,
)
from src.db.base import get_session
from src.db.models import User
from src.db import repositories as repo


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# Pick provider once at import. Mock is the default until IT grants real O365.
def _build_provider() -> AuthProvider:
    name = os.environ.get("AUTH_PROVIDER", "mock").lower()
    if name == "microsoft":
        from src.auth.microsoft import MicrosoftProvider
        return MicrosoftProvider()
    return MockProvider()


_provider = _build_provider()
logger.info(f"[auth] active provider: {_provider.name}")

# CSRF state store — process-local since this is single-process dev. For
# multi-worker prod, move to Redis or a signed cookie. Real O365 requires
# state validation; mock provider skips this (no impersonation risk in dev).
_state_store: dict[str, float] = {}
_STATE_TTL = 600  # 10 minutes — user has 10 min to complete login


def _new_state() -> str:
    import time
    state = secrets.token_urlsafe(24)
    _state_store[state] = time.time() + _STATE_TTL
    # Garbage-collect expired states (lazy cleanup, no background task).
    now = time.time()
    expired = [s for s, ts in _state_store.items() if ts < now]
    for s in expired:
        _state_store.pop(s, None)
    return state


def _consume_state(state: str) -> bool:
    """Returns True if state was valid + unconsumed. Single-use."""
    import time
    expiry = _state_store.pop(state, None)
    return expiry is not None and expiry > time.time()


def _enroll_optional() -> bool:
    """VOICE_ENROLL_OPTIONAL=true skips the voice-enrollment gate. Set it on
    deploys that don't ship the local pyannote/torch embedding stack (e.g. the
    single-port AgentBase image), so login lands on /app instead of the
    enrollment flow that would 500 on the missing dependency."""
    return os.environ.get("VOICE_ENROLL_OPTIONAL", "").strip().lower() == "true"


def _enrollment_satisfied(user) -> bool:
    """Whether the enrollment gate is cleared — really enrolled, or bypassed
    via VOICE_ENROLL_OPTIONAL. Used by both the callback redirect and /auth/me
    (which the FE reads to route /onboard/voice vs /app)."""
    return bool(user.voice_enrolled) or _enroll_optional()


def _redirect_uri(request: Request) -> str:
    """Build the absolute /auth/callback URL Microsoft will redirect to.

    MS_REDIRECT_URI pins the value to the URL registered in Azure. Required in
    dev: Vite (:8001) proxies /auth → backend (:8002) with changeOrigin, which
    rewrites the host to :8002 — so deriving it from the request would produce
    :8002 and break OAuth's exact-match on the registered :8001 URL.

    Without the override, derive from the request (honors X-Forwarded-* so it
    still works behind a TLS-terminating proxy in prod).
    """
    override = os.environ.get("MS_REDIRECT_URI")
    if override:
        return override
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}/auth/callback"


# ─── GET /auth/login ────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request, next: Optional[str] = None):
    """Kick off the OAuth flow — redirect to the provider's login URL.

    `?next=/path` is an optional return URL stashed in the state so the
    callback can bounce the user back to where they were heading.
    """
    state = _new_state()
    if next:
        # Pack the post-login destination into state (simple "state|next" form).
        # The callback splits on the first "|".
        state_with_next = f"{state}|{next}"
        _state_store[state_with_next] = _state_store.pop(state)
        state = state_with_next
    url = _provider.get_login_url(state=state, redirect_uri=_redirect_uri(request))
    return RedirectResponse(url, status_code=302)


# ─── /auth/mock-login (only used when AUTH_PROVIDER=mock) ──────────

_MOCK_LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Sign in to your account</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  *,*::before,*::after{box-sizing:border-box}
  body{margin:0;font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
       background:#f2f2f2;min-height:100vh;display:flex;align-items:center;justify-content:center;color:#1b1b1b}
  .mock-banner{position:fixed;top:0;left:0;right:0;background:#fef3c7;color:#92400e;
               text-align:center;padding:8px 12px;font-size:13px;font-weight:600;
               border-bottom:1px solid #fde68a;letter-spacing:.02em;z-index:10}
  .card{background:white;width:440px;padding:44px;box-shadow:0 2px 6px rgba(0,0,0,.18);
        border:1px solid #c8c6c4}
  .logo{display:flex;gap:6px;align-items:center;margin-bottom:24px;font-size:15px;color:#5e5e5e}
  .logo-mark{display:inline-grid;grid-template-columns:1fr 1fr;gap:2px;width:24px;height:24px}
  .logo-mark span{display:block;width:100%;height:100%}
  .logo-mark span:nth-child(1){background:#f25022}
  .logo-mark span:nth-child(2){background:#7fba00}
  .logo-mark span:nth-child(3){background:#00a4ef}
  .logo-mark span:nth-child(4){background:#ffb900}
  h1{font-size:24px;font-weight:600;margin:0 0 24px;color:#1b1b1b}
  label{display:block;font-size:13px;margin:14px 0 4px;color:#1b1b1b}
  input[type=email],input[type=text]{
    width:100%;padding:6px 8px;font-size:15px;border:none;
    border-bottom:1px solid #1b1b1b;outline:none;background:transparent;
    font-family:inherit;
  }
  input[type=email]:focus,input[type=text]:focus{border-bottom:2px solid #0067b8}
  .help{margin:14px 0;font-size:13px}
  .help a{color:#0067b8;text-decoration:none}
  .help a:hover{text-decoration:underline}
  .actions{display:flex;justify-content:flex-end;margin-top:28px;gap:8px}
  button{font-family:inherit;font-size:15px;padding:6px 12px;min-width:108px;
         border:1px solid #8a8886;background:white;color:#1b1b1b;cursor:pointer}
  button.primary{background:#0067b8;color:white;border-color:#0067b8}
  button.primary:hover{background:#005a9e}
  .signin-options{margin-top:36px;display:flex;align-items:center;gap:8px;
                  font-size:13px;color:#1b1b1b;cursor:pointer}
  .signin-options::before{content:'';width:16px;height:16px;border:1px solid #1b1b1b;border-radius:50%;display:inline-block}
  .footer{position:fixed;bottom:0;right:0;font-size:11px;color:#7a7a7a;padding:12px 20px;background:#f2f2f2}
</style>
</head>
<body>
  <div class="mock-banner">⚠ MOCK LOGIN · Dev mode · No real authentication · enable real O365 by setting AUTH_PROVIDER=microsoft</div>
  <form class="card" method="POST" action="/auth/mock-submit">
    <div class="logo">
      <div class="logo-mark"><span></span><span></span><span></span><span></span></div>
      <span>Microsoft</span>
    </div>
    <h1>Sign in</h1>
    <input type="hidden" name="state" value="__STATE__" />
    <input type="hidden" name="redirect_uri" value="__REDIRECT__" />
    <label for="email">Email, phone, or Skype</label>
    <input type="email" name="email" id="email" required autofocus
           placeholder="someone@example.com" />
    <label for="name" style="margin-top:18px">Display name</label>
    <input type="text" name="name" id="name"
           placeholder="An Nguyễn" />
    <div class="help"><a href="#">No account? Create one!</a></div>
    <div class="help"><a href="#">Can't access your account?</a></div>
    <div class="actions">
      <button type="submit" class="primary">Next</button>
    </div>
    <div class="signin-options">Sign-in options</div>
  </form>
  <div class="footer">Terms of use &middot; Privacy &amp; cookies &middot; …</div>
</body>
</html>"""


@router.get("/mock-login", response_class=HTMLResponse)
async def mock_login_page(state: str, redirect_uri: str):
    """Render the fake MS-style login page. Only reachable when
    AUTH_PROVIDER=mock — the real provider redirects to login.microsoftonline.com.
    """
    if not isinstance(_provider, MockProvider):
        raise HTTPException(404)
    html = _MOCK_LOGIN_PAGE.replace("__STATE__", state).replace("__REDIRECT__", redirect_uri)
    return HTMLResponse(html)


@router.post("/mock-submit")
async def mock_submit(
    request: Request,
    email: str = Form(...),
    name: Optional[str] = Form(None),
    state: str = Form(...),
    redirect_uri: str = Form(...),
):
    """Mock form post → redirect to /auth/callback with a packed mock code.
    The user-facing flow is identical to real OAuth at this point.
    """
    if not isinstance(_provider, MockProvider):
        raise HTTPException(404)
    info = UserInfo(
        email=email.strip().lower(),
        display_name=(name or "").strip() or email.split("@")[0],
    )
    code = encode_mock_code(info)
    return RedirectResponse(f"/auth/callback?code={code}&state={state}", status_code=302)


# ─── GET /auth/callback ──────────────────────────────────────────────

@router.get("/callback")
async def callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """Provider redirects here after auth. Exchange code → user info →
    upsert User → issue session cookie → redirect to app (or `next`).
    """
    if error:
        logger.warning(f"[auth] provider error: {error} {error_description}")
        return RedirectResponse(f"/?auth_error={error}", status_code=302)
    if not code:
        raise HTTPException(400, "Missing code")

    # Validate state (CSRF) only for providers that need it. Mock skips this
    # since the threat model doesn't apply to a dev page.
    next_path = "/"
    if _provider.requires_csrf_state:
        if not state or not _consume_state(state):
            raise HTTPException(400, "Invalid or expired state — please retry login")
    if state and "|" in state:
        # Extract the post-login destination packed into state by /auth/login.
        _, next_path = state.split("|", 1)
        if not next_path.startswith("/"):
            next_path = "/"

    # Provider-specific code exchange. Any ValueError = invalid code.
    try:
        info = _provider.exchange_code(code=code, redirect_uri=_redirect_uri(request))
    except ValueError as e:
        logger.warning(f"[auth] code exchange failed: {e}")
        raise HTTPException(401, f"Authentication failed: {e}")

    # Upsert user by email (works for both mock + real providers since email
    # is unique). On real O365 we ALSO populate ms_oid / ms_tenant_id so
    # token-refresh + Graph API calls work later.
    user = await _upsert_user(session, info)
    await session.commit()

    # Issue signed session cookie + bounce to the right route.
    # `next_path` from /auth/login state takes priority when set; otherwise:
    #   - first-time login → /onboard/voice (gate to enroll voice)
    #   - returning user   → /app (main workspace)
    cookie_value = issue_session_cookie(user.id, user.email)
    if not _enrollment_satisfied(user):
        target = "/onboard/voice"
    elif next_path and next_path != "/":
        target = next_path
    else:
        target = "/app"
    resp = RedirectResponse(target, status_code=302)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=cookie_value,
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # secure=True in prod; off in dev to allow http://localhost
        secure=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
        path="/",
    )
    return resp


async def _resolve_role_id(session: AsyncSession, position: Optional[str]):
    """jobTitle → role name → role_id, or None. Best-effort: never raises."""
    if not position:
        return None
    try:
        name = await repo.resolve_role_by_title(session, position)
        if not name:
            return None
        role = await repo.get_role(session, name)
        return role.id if role else None
    except Exception:
        logger.warning("role resolution failed for position=%r", position)
        return None


async def _upsert_user(session: AsyncSession, info: UserInfo) -> User:
    """Look up by email first (works for both providers). On first sight,
    create the row; on returning, just refresh display_name/avatar/last_login.
    """
    stmt = select(User).where(User.email == info.email)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user:
        # Refresh fields that may have changed in the IdP (display name on
        # marriage, photo update, tenant move).
        if info.display_name:
            user.display_name = info.display_name
        if info.avatar_url:
            user.avatar_url = info.avatar_url
        if info.ms_oid and not user.ms_oid:
            user.ms_oid = info.ms_oid
        if info.ms_tenant_id and not user.ms_tenant_id:
            user.ms_tenant_id = info.ms_tenant_id
        # Refresh the stored MSAL token cache (encrypted) on every login so the
        # refresh token stays current for minting Graph access tokens later.
        if info.ms_token_cache:
            user.refresh_token = encrypt_token(info.ms_token_cache)
        user.role_id = await _resolve_role_id(session, info.position)
        user.position = info.position
        user.last_login_at = datetime.now(timezone.utc)
        return user

    user = User(
        email=info.email,
        display_name=info.display_name,
        avatar_url=info.avatar_url,
        ms_oid=info.ms_oid,
        ms_tenant_id=info.ms_tenant_id,
        refresh_token=encrypt_token(info.ms_token_cache) if info.ms_token_cache else None,
        role_id=await _resolve_role_id(session, info.position),
        position=info.position,
        voice_enrolled=False,
        last_login_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.flush()
    logger.info(f"[auth] created user {user.id} email={info.email} provider={_provider.name}")
    return user


# ─── POST /auth/logout ───────────────────────────────────────────────

@router.post("/logout")
async def logout():
    """Clear the session cookie. FE typically follows with a redirect to /."""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=COOKIE_NAME, path="/")
    return resp


# ─── GET /auth/me ───────────────────────────────────────────────────

@router.get("/me")
async def me(user: Optional[User] = Depends(get_current_user_optional)):
    """Current user info. Returns 401 if no valid session — FE uses this on
    page load to decide: anonymous → landing, no-voice → /onboard/voice,
    fully authed → app.
    """
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "voice_enrolled": _enrollment_satisfied(user),
        "provider": _provider.name,
    }
