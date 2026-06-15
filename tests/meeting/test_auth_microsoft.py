"""Unit tests for MicrosoftProvider (real O365 OAuth via MSAL).

No network: a fake MSAL app stands in for msal.ConfidentialClientApplication.
`acquire_token_by_authorization_code` does real network I/O against Microsoft,
so it's the one dependency we inject. Claim extraction + error handling — the
provider's actual logic — are tested against recorded MSAL result shapes.
"""
from __future__ import annotations

import pytest

from meeting.auth.base import UserInfo


CLIENT_ID = "821cfa9b-972b-421a-99cf-4cc3db53fc71"
TENANT_ID = "7c112a6e-10e2-4e09-afc4-2e37bc60d821"
REDIRECT = "http://localhost:8001/auth/callback"


class _FakeMsalApp:
    """Stand-in for msal.ConfidentialClientApplication."""

    def __init__(self, token_result: dict):
        self._token_result = token_result
        self.calls: dict = {}

    def get_authorization_request_url(self, scopes, state=None, redirect_uri=None):
        self.calls["authorize"] = {"scopes": scopes, "state": state, "redirect_uri": redirect_uri}
        return f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize?state={state}"

    def acquire_token_by_authorization_code(self, code, scopes, redirect_uri):
        self.calls["exchange"] = {"code": code, "scopes": scopes, "redirect_uri": redirect_uri}
        return self._token_result


def _provider(monkeypatch, token_result: dict):
    monkeypatch.setenv("MS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret-value")
    monkeypatch.setenv("MS_TENANT_ID", TENANT_ID)
    from meeting.auth.microsoft import MicrosoftProvider
    return MicrosoftProvider(msal_app=_FakeMsalApp(token_result))


def test_exchange_code_extracts_userinfo_from_id_token_claims(monkeypatch):
    result = {
        "access_token": "graph-token",
        "id_token_claims": {
            "oid": "9c1f8e7a-1111-2222-3333-444455556666",
            "tid": TENANT_ID,
            "preferred_username": "An.Nguyen@VNG.com.vn",
            "name": "An Nguyễn",
        },
    }
    provider = _provider(monkeypatch, result)

    info = provider.exchange_code(code="auth-code-abc", redirect_uri=REDIRECT)

    assert isinstance(info, UserInfo)
    assert info.email == "an.nguyen@vng.com.vn"          # lowercased
    assert info.display_name == "An Nguyễn"
    assert info.ms_oid == "9c1f8e7a-1111-2222-3333-444455556666"
    assert info.ms_tenant_id == TENANT_ID


def test_exchange_code_raises_valueerror_on_msal_error(monkeypatch):
    result = {"error": "invalid_grant", "error_description": "AADSTS70008: expired code"}
    provider = _provider(monkeypatch, result)

    with pytest.raises(ValueError):
        provider.exchange_code(code="bad-code", redirect_uri=REDIRECT)


def test_get_login_url_passes_state_and_redirect(monkeypatch):
    provider = _provider(monkeypatch, {})

    url = provider.get_login_url(state="st-123", redirect_uri=REDIRECT)

    assert "state=st-123" in url
    call = provider._injected_app.calls["authorize"]
    assert call["state"] == "st-123"
    assert call["redirect_uri"] == REDIRECT


def test_init_requires_client_credentials(monkeypatch):
    monkeypatch.delenv("MS_CLIENT_ID", raising=False)
    monkeypatch.delenv("MS_CLIENT_SECRET", raising=False)
    from meeting.auth.microsoft import MicrosoftProvider
    with pytest.raises(RuntimeError):
        MicrosoftProvider()


def test_fetch_profile_parses_graph_response(monkeypatch):
    import meeting.auth.microsoft as ms
    monkeypatch.setattr(
        ms, "_graph_get_me",
        lambda token: {"jobTitle": "Applied AI Engineer", "department": "Engineer"},
    )
    provider = _provider(monkeypatch, {"access_token": "graph-token"})
    prof = provider.fetch_profile("graph-token")
    assert prof["job_title"] == "Applied AI Engineer"
    assert prof["department"] == "Engineer"


def test_fetch_profile_degrades_to_empty_on_graph_error(monkeypatch):
    import meeting.auth.microsoft as ms
    def _boom(token):
        raise RuntimeError("graph 500")
    monkeypatch.setattr(ms, "_graph_get_me", _boom)
    provider = _provider(monkeypatch, {"access_token": "graph-token"})
    assert provider.fetch_profile("graph-token") == {}


def test_exchange_code_sets_position_from_graph(monkeypatch):
    import meeting.auth.microsoft as ms
    monkeypatch.setattr(
        ms, "_graph_get_me",
        lambda token: {"jobTitle": "Software Engineer", "department": "Product"},
    )
    result = {
        "access_token": "graph-token",
        "id_token_claims": {
            "oid": "9c1f8e7a-1111-2222-3333-444455556666",
            "tid": TENANT_ID,
            "preferred_username": "An.Nguyen@VNG.com.vn",
            "name": "An Nguyễn",
        },
    }
    provider = _provider(monkeypatch, result)
    info = provider.exchange_code(code="auth-code-abc", redirect_uri=REDIRECT)
    assert info.position == "Software Engineer"
    assert info.department == "Product"


def test_exchange_code_position_none_when_graph_fails(monkeypatch):
    import meeting.auth.microsoft as ms
    monkeypatch.setattr(ms, "_graph_get_me", lambda token: (_ for _ in ()).throw(RuntimeError("x")))
    result = {
        "access_token": "graph-token",
        "id_token_claims": {
            "oid": "o", "tid": TENANT_ID,
            "preferred_username": "a@vng.com.vn", "name": "A",
        },
    }
    provider = _provider(monkeypatch, result)
    info = provider.exchange_code(code="c", redirect_uri=REDIRECT)
    assert info.position is None
