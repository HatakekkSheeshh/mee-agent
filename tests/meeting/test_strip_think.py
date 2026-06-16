"""strip_think — remove leaked <think>…</think> reasoning from a chat reply.

Some serving layers emit Qwen3/minimax chain-of-thought as raw `<think>` text in
the message content. Prompt-steering isn't reliable, so we strip it deterministically
before the reply reaches the user.
"""
from __future__ import annotations

from meeting.graphs._chat_serde import strip_think


def test_strip_removes_complete_think_block_keeps_answer():
    out = strip_think("<think>user hỏi tên\nđể mình nghĩ</think>Bạn là Ronaldo.")
    assert out == "Bạn là Ronaldo."


def test_strip_removes_multiline_and_is_case_insensitive():
    raw = "<THINK>\nline 1\nline 2\n</Think>\n\nĐáp án cuối."
    assert strip_think(raw) == "Đáp án cuối."


def test_strip_removes_unclosed_think_from_truncation():
    # max_tokens cut off before the closing tag → drop from <think> to the end
    assert strip_think("Mở đầu. <think>đang suy nghĩ thì bị cắt") == "Mở đầu."


def test_strip_noop_without_think_tag():
    assert strip_think("Câu trả lời bình thường.") == "Câu trả lời bình thường."


def test_strip_handles_empty_and_none():
    assert strip_think("") == ""
    assert strip_think(None) == ""
