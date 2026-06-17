"""The MCP client builds its Authorization header from a per-call key when
given one, else the constructed (env) key. Pure header logic — no network."""
from src.services.redmine_mcp_client import RedmineMcpClient


def _client():
    return RedmineMcpClient(base_url="https://mcp.example/mcp", api_key="env-key")


def test_auth_headers_uses_per_call_key():
    c = _client()
    assert c._auth_headers("user-key") == {"Authorization": "Bearer user-key"}


def test_auth_headers_falls_back_to_env_key():
    c = _client()
    assert c._auth_headers(None) == {"Authorization": "Bearer env-key"}


def test_auth_headers_empty_when_no_key():
    c = RedmineMcpClient(base_url="https://mcp.example/mcp", api_key="")
    assert c._auth_headers(None) == {}
