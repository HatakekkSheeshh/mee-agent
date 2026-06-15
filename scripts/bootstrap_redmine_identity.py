"""One-shot: provision the `mee` agent identity + `redmine` delegated provider
on AgentBase Identity. Idempotent (409 Conflict → treated as already-exists).

Run ONCE per environment after setting GREENNODE_CLIENT_ID/SECRET and
AGENTBASE_REDMINE_RETURN_URL in .env:

    venv/bin/python scripts/bootstrap_redmine_identity.py

Pure payload builders are unit-tested; the network calls reuse the proven IAM
token flow from meeting.memory_client._get_token.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

from meeting.memory_client import _get_token
from meeting.services.identity_client import ALLOWED_IDENTITY_HOST, DEFAULT_IDENTITY_BASE


def provider_payload(name: str) -> dict:
    return {"name": name}


def identity_payload(name: str, allowed_return_urls: list[str]) -> dict:
    return {"name": name, "allowedReturnUrls": allowed_return_urls}


def parse_allowed_return_urls(allowed_csv: str, single: str) -> list[str]:
    """Whitelist for the identity's allowedReturnUrls.

    Prefers the comma-separated AGENTBASE_REDMINE_ALLOWED_RETURN_URLS (so dev AND
    prod URLs can be whitelisted in one go), falling back to the single
    per-request AGENTBASE_REDMINE_RETURN_URL. De-duped, order-preserving.
    """
    raw = allowed_csv or single
    seen: list[str] = []
    for part in raw.split(","):
        u = part.strip()
        if u and u not in seen:
            seen.append(u)
    return seen


def _post(base: str, path: str, body: dict, token: str) -> tuple[int, str]:
    url = f"{base.rstrip('/')}{path}"
    if ALLOWED_IDENTITY_HOST not in url:
        raise ValueError(f"refusing non-allowlisted URL: {url}")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    load_dotenv(override=True, interpolate=False)
    base = os.getenv("AGENTBASE_IDENTITY_URL", DEFAULT_IDENTITY_BASE)
    identity = os.getenv("AGENTBASE_AGENT_IDENTITY", "mee")
    provider = os.getenv("REDMINE_DELEGATED_PROVIDER", "redmine")
    return_url = os.getenv("AGENTBASE_REDMINE_RETURN_URL", "")
    if not return_url:
        print("ERROR: set AGENTBASE_REDMINE_RETURN_URL in .env first", file=sys.stderr)
        return 2

    allowed = parse_allowed_return_urls(
        os.getenv("AGENTBASE_REDMINE_ALLOWED_RETURN_URLS", ""), return_url
    )
    token = _get_token()

    st, body = _post(base, "/agent-identities", identity_payload(identity, allowed), token)
    print(f"create identity {identity!r} allowedReturnUrls={allowed}: HTTP {st} {body[:300]}")
    if st == 409:
        print("  NOTE: identity already exists — its allowedReturnUrls was NOT updated. "
              "To add a URL, delete+recreate the identity (user keys live under the "
              "provider, not the identity, so none are lost) or update it in the console.")
    elif st not in (200, 201):
        return 1

    st, body = _post(base, "/outbound-auth/delegated-api-key-providers", provider_payload(provider), token)
    print(f"create provider {provider!r}: HTTP {st} {body[:300]}")
    if st not in (200, 201, 409):
        return 1

    print("Bootstrap complete (409 = already existed, which is fine).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
