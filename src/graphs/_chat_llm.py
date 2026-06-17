"""LLM client helpers for the chat graph.

Extracted from chat_graph.py and re-imported there. The callers (classify_intent,
make_agent) stay in chat_graph, so they resolve these via chat_graph's module
globals — tests that monkeypatch `chat_graph._llm_client` still take effect.
"""
from __future__ import annotations

import os

from openai import OpenAI


def _llm_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"),
    )


def _llm_model() -> str:
    return os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
