"""Thin async client for GreenNode AgentBase Identity (outbound-auth).

Resolves each end-user's own Redmine API key from the `delegated` API-key
provider, keyed by the user's Azure OID (agentUserId = users.ms_oid). Auth to
AgentBase is IAM client-credentials (GREENNODE_CLIENT_ID/SECRET), the same flow
proven in meeting/memory_client.py — here async (httpx) with an injectable
transport so the parsing/URL logic is unit-tested offline.

The raw Redmine key never passes through Mee's chat/LLM/logs: get_user_key only
returns it to the per-call MCP Bearer; the consent flow is a hosted redirect.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ALLOWED_IDENTITY_HOST = "agentbase.api.vngcloud.vn"
IAM_TOKEN_URL = "https://iam.api.vngcloud.vn/accounts-api/v2/auth/token"
DEFAULT_IDENTITY_BASE = "https://agentbase.api.vngcloud.vn/identity/api/v1"


@dataclass(frozen=True)
class RequestKeyResult:
    apikey: Optional[str]
    authorization_url: Optional[str]
    status: Optional[str]


def _first(body: dict, *keys: str):
    for k in keys:
        if k in body and body[k] is not None:
            return body[k]
    return None


def parse_request_key_response(body: Optional[dict]) -> RequestKeyResult:
    """Tolerant parse of the request-key response (camelCase OR snake_case)."""
    body = body or {}
    return RequestKeyResult(
        apikey=_first(body, "apikey", "apiKey", "api_key"),
        authorization_url=_first(body, "authorization_url", "authorizationUrl", "authorizationURL"),
        status=_first(body, "status"),
    )


def build_request_key_url(base_url: str, provider_name: str, agent_identity: str) -> str:
    base = base_url.rstrip("/")
    return (
        f"{base}/outbound-auth/delegated-api-key-providers/{provider_name}"
        f"/agent-identities/{agent_identity}/api-key"
    )


def pick_return_url(raw: str) -> str:
    """AGENTBASE_REDMINE_RETURN_URL may list several allowed return URLs
    (comma-separated, one per deployment), but AgentBase Identity's ``returnUrl``
    must be a SINGLE value. The active deployment lists its URL FIRST; this
    returns that first non-empty entry (a single value is returned unchanged).
    Register all listed URLs in the AgentBase provider allowlist.
    """
    for u in (raw or "").split(","):
        u = u.strip()
        if u:
            return u
    return ""


class IdentityClient:
    def __init__(
        self,
        *,
        base_url: str,
        agent_identity: str,
        provider_name: str,
        client_id: str,
        client_secret: str,
        return_url: str,
        timeout: float = 20.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if ALLOWED_IDENTITY_HOST not in (base_url or ""):
            raise ValueError(f"Identity base_url must be on {ALLOWED_IDENTITY_HOST}: {base_url!r}")
        self._base_url = base_url.rstrip("/")
        self._agent_identity = agent_identity
        self._provider = provider_name
        self._client_id = client_id
        self._client_secret = client_secret
        self._return_url = return_url
        self._timeout = timeout
        self._transport = transport
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    async def _get_token(self) -> str:
        now = time.time()
        if self._token and self._token_exp > now + 60:
            return self._token
        if not self._client_id or not self._client_secret:
            raise RuntimeError("Missing GREENNODE_CLIENT_ID / GREENNODE_CLIENT_SECRET")
        auth = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(
                IAM_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = now + float(data.get("expires_in", 3600))
        return self._token

    async def request_user_key(
        self, agent_user_id: str, return_url: Optional[str] = None
    ) -> RequestKeyResult:
        token = await self._get_token()
        url = build_request_key_url(self._base_url, self._provider, self._agent_identity)
        payload = {
            "agentUserId": agent_user_id,
            "returnUrl": return_url or pick_return_url(self._return_url),
        }
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(
                url, json=payload, headers={"Authorization": f"Bearer {token}"}
            )
        resp.raise_for_status()
        return parse_request_key_response(resp.json())

    async def get_user_key(self, agent_user_id: str) -> Optional[str]:
        """The user's stored Redmine key, or None if not yet authorized."""
        try:
            return (await self.request_user_key(agent_user_id)).apikey
        except Exception as e:  # fail closed; never leak / never crash the turn
            logger.warning("identity get_user_key failed for %s: %s", agent_user_id, e)
            return None


_singleton: Optional[IdentityClient] = None
_key_cache: dict[str, tuple[Optional[str], float]] = {}
DEFAULT_KEY_TTL = 300.0  # seconds


def get_identity_client() -> IdentityClient:
    """Lazy env singleton (mirrors get_redmine_mcp_client / get_pm_agent_client)."""
    global _singleton
    if _singleton is None:
        _singleton = IdentityClient(
            base_url=os.getenv("AGENTBASE_IDENTITY_URL", DEFAULT_IDENTITY_BASE),
            agent_identity=os.getenv("AGENTBASE_AGENT_IDENTITY", "mee"),
            provider_name=os.getenv("REDMINE_DELEGATED_PROVIDER", "redmine"),
            client_id=os.getenv("GREENNODE_CLIENT_ID", ""),
            client_secret=os.getenv("GREENNODE_CLIENT_SECRET", ""),
            return_url=os.getenv("AGENTBASE_REDMINE_RETURN_URL", ""),
        )
    return _singleton


def clear_key_cache() -> None:
    _key_cache.clear()


async def get_cached_user_key(agent_user_id: str, *, now=time.time, ttl: float = DEFAULT_KEY_TTL) -> Optional[str]:
    """TTL-cached per-user key resolution (negative results cached too)."""
    hit = _key_cache.get(agent_user_id)
    t = now()
    if hit is not None and hit[1] > t:
        return hit[0]
    key = await get_identity_client().get_user_key(agent_user_id)
    _key_cache[agent_user_id] = (key, t + ttl)
    return key
