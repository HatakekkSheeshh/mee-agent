"""Probe: does the configured gemma MaaS endpoint honor forced tool calls?

Task 0 of the force-grounding plan
(docs/superpowers/plans/2026-06-10-force-grounding-recording-scoped.md).

Tests two forcing modes against the live LLM:
  1. tool_choice="required"  → model MUST emit some tool_call.
  2. tool_choice={"type":"function","function":{"name":"list_recordings"}}
                             → model MUST emit that specific tool_call.

The user message is deliberately answerable from "memory" (no real data),
to check the endpoint forces a tool call even when the model would rather
answer directly — exactly the stale-summary failure mode we are fixing.

Run: ECC_GATEGUARD=off venv/bin/python scripts/probe_tool_choice_required.py
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
print(f"base_url={os.getenv('LLM_BASE_URL')!r} model={model!r}\n")

tools = [
    {
        "type": "function",
        "function": {
            "name": "list_recordings",
            "description": "List the recordings (phiên/sessions) of the bound meeting/project.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recording_mom",
            "description": "Read the Biên bản (MoM) of one recording by id.",
            "parameters": {
                "type": "object",
                "properties": {"recording_id": {"type": "string"}},
                "required": ["recording_id"],
            },
        },
    },
]

messages = [
    {
        "role": "system",
        "content": "Bạn là Mee, trợ lý cuộc họp. Trả lời ngắn gọn bằng tiếng Việt.",
    },
    {
        "role": "user",
        "content": "Tóm tắt phiên họp Meeting 1.",
    },
]


def run(label: str, tool_choice) -> bool:
    print(f"=== {label}: tool_choice={tool_choice!r} ===")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=512,
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001 - probe wants the raw failure
        print(f"  !!! request FAILED: {type(e).__name__}: {e}")
        print("  → endpoint rejected this tool_choice form.\n")
        return False

    msg = resp.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None)
    print(f"  finish_reason: {resp.choices[0].finish_reason}")
    print(f"  content: {msg.content!r}")
    print(f"  tool_calls: {tool_calls}")
    if tool_calls:
        tc = tool_calls[0]
        print(f"  → forced a tool call: {tc.function.name}({tc.function.arguments})")
        try:
            json.loads(tc.function.arguments or "{}")
        except Exception as e:  # noqa: BLE001
            print(f"  (arguments not valid JSON: {e})")
        print()
        return True
    print("  → NO tool_calls; model answered directly despite forcing.\n")
    return False


required_ok = run("MODE 1 required", "required")
forced_ok = run(
    "MODE 2 forced-function",
    {"type": "function", "function": {"name": "list_recordings"}},
)

print("================ VERDICT ================")
if required_ok:
    print('tool_choice="required" HONORED → Task 3 uses "required".')
elif forced_ok:
    print('Only forced-function HONORED → Task 3 uses the {"type":"function",...} form.')
else:
    print("NEITHER honored → skip mechanical force; ship Task 4 (prompt) only.")
