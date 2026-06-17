"""One-shot probe: does the configured LLM support native OpenAI tool-calling?

Decides Task #8 architecture:
  - Reliable tool_calls in the response → Path A (native tool-calling agent).
  - No/empty tool_calls → Path B (structured-JSON ReAct loop).

Run: venv/bin/python scripts/probe_tool_calling.py
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True, interpolate=False)

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", ""),
)
model = os.getenv("LLM_MODEL", "")
print(f"base_url={os.getenv('LLM_BASE_URL')!r} model={model!r}")

tools = [
    {
        "type": "function",
        "function": {
            "name": "retrieve",
            "description": "Search the bound meeting's transcript and minutes for content "
            "relevant to a query. Call this whenever you need meeting content to answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                },
                "required": ["query"],
            },
        },
    }
]

messages = [
    {
        "role": "system",
        "content": "Bạn là Mee, trợ lý cuộc họp. Khi cần nội dung cuộc họp để trả lời, "
        "hãy GỌI tool `retrieve`. Đừng bịa nội dung.",
    },
    {
        "role": "user",
        "content": "Cuộc họp tuần trước đã quyết định gì về việc deploy v1?",
    },
]

try:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        max_tokens=512,
        timeout=60,
    )
except Exception as e:
    print(f"\n!!! tools=[...] request FAILED: {type(e).__name__}: {e}")
    print("VERDICT: likely Path B (endpoint rejects the tools param or tool schema)")
    raise SystemExit(1)

msg = resp.choices[0].message
tool_calls = getattr(msg, "tool_calls", None)
print("\n--- response.message ---")
print("content:", repr(msg.content))
print("tool_calls:", tool_calls)
print("finish_reason:", resp.choices[0].finish_reason)

if tool_calls:
    tc = tool_calls[0]
    print("\nfirst tool_call.function.name:", tc.function.name)
    print("first tool_call.function.arguments:", tc.function.arguments)
    try:
        json.loads(tc.function.arguments)
        print("\nVERDICT: Path A — native tool-calling works (parseable arguments).")
    except Exception as e:
        print(f"\nVERDICT: Path A-ish but arguments not valid JSON: {e}")
else:
    print("\nVERDICT: Path B — no tool_calls returned; use structured-JSON loop.")
