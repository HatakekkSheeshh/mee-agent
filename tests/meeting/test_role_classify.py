"""classify_role — LLM fallback that maps an unmatched jobTitle into the pool.

`generate(messages) -> str` is injected so no network is needed. Guards: the
returned role name must be in the pool; below the confidence threshold → None;
hallucinated/NONE answers → None.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.services.role_mapping import classify_role


def _role(name):
    return SimpleNamespace(name=name, description="", data_plan="own_tasks", aliases=[])


ROLES = [_role("AI Applied"), _role("Software Engineer"), _role("Business Analyst")]


def test_confident_match_returns_pool_role():
    gen = lambda messages: '{"role": "Software Engineer", "confidence": 0.92}'
    assert classify_role("Senior Backend Developer", ROLES, generate=gen) == "Software Engineer"


def test_below_threshold_returns_none():
    gen = lambda messages: '{"role": "Software Engineer", "confidence": 0.3}'
    assert classify_role("Office Cat", ROLES, generate=gen) is None


def test_out_of_pool_name_rejected():
    gen = lambda messages: '{"role": "Chief Vibes Officer", "confidence": 0.99}'
    assert classify_role("Vibes Lead", ROLES, generate=gen) is None


def test_none_answer_returns_none():
    gen = lambda messages: "NONE"
    assert classify_role("Unknown", ROLES, generate=gen) is None


def test_strips_think_and_parses():
    gen = lambda messages: '<think>hmm</think>{"role": "AI Applied", "confidence": 0.8}'
    assert classify_role("Applied AI Researcher", ROLES, generate=gen) == "AI Applied"


def test_llm_garbage_returns_none():
    gen = lambda messages: "I think maybe a software engineer probably?"
    assert classify_role("x", ROLES, generate=gen) is None


def test_generate_raises_returns_none():
    def bad_gen(messages):
        raise RuntimeError("network error")
    assert classify_role("Developer", ROLES, generate=bad_gen) is None
