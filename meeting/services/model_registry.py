"""Model registry — STT + LLM profile metadata + per-recording resolver.

USAGE
  User picks an STT/LLM model in the UI; the choice persists on
  `recordings.stt_model` / `recordings.llm_model` (or `meetings.*` as project
  default). Backends ask this module to RESOLVE the choice into a concrete
  {base_url, api_key, model, ...} dict at runtime.

  - resolve_stt(recording, meeting) → STT profile dict
  - resolve_llm(recording, meeting) → LLM profile dict
  - get_profiles(kind) → list for the UI dropdown

DESIGN
  - Logical model IDs (e.g. "gemma", "qwen") are STABLE — UI + DB use these.
  - The actual base_url / api_key / model name live in env vars per profile.
  - Each profile carries `label` + `description` for the UI dropdown.
  - Falls back to the legacy LLM_*/WHISPER_* env vars when a profile isn't
    explicitly configured (so old .env keeps working).

ADDING A NEW PROFILE
  1. Add an entry to STT_PROFILES or LLM_PROFILES below.
  2. Document the env vars in .env.example.
  3. The UI picks it up automatically via /api/models.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ─── STT profiles ──────────────────────────────────────────────────────
# `language` is the value passed to Whisper-compatible APIs (vi/en/auto).
# Self-hosted PhoWhisper ignores it — it's Vietnamese-only by design.
STT_PROFILES: dict[str, dict] = {
    "whisper": {
        "id": "whisper",
        "label": "Whisper Multilingual (mặc định)",
        "description": (
            "Phù hợp cuộc họp có tiếng Anh hoặc pha trộn Việt-Anh."
        ),
        "env": {
            "base_url": "WHISPER_BASE_URL",
            "api_key": "WHISPER_API_KEY",
            "model": "WHISPER_MODEL",
        },
        "language": "auto",
        "has_diarization": False,
    },
    "phowhisper": {
        "id": "phowhisper",
        "label": "PhoWhisper (Tiếng Việt)",
        "description": (
            "Tối ưu cho cuộc họp hoàn toàn bằng tiếng Việt."
        ),
        "env": {
            "base_url": "PHOWHISPER_BASE_URL",
            "api_key": "PHOWHISPER_API_KEY",
            "model": "PHOWHISPER_MODEL",
        },
        "language": "vi",
        "has_diarization": True,
    },
}

# ─── LLM profiles ──────────────────────────────────────────────────────
# Used by transcript_cleaner + phonetic_generator + note_generator + chat_graph.
# Default if nothing else picks: "gemma".
LLM_PROFILES: dict[str, dict] = {
    "gemma": {
        "id": "gemma",
        "label": "Gemma 4 (31B) — mặc định",
        "description": (
            "Nhanh, phù hợp đa số cuộc họp. Chọn cái này nếu phân vân."
        ),
        "env": {
            "base_url": "GEMMA_BASE_URL",
            "api_key": "GEMMA_API_KEY",
            "model": "GEMMA_MODEL",
        },
        "context_chars": 60_000,
        "max_tokens": 16_000,
    },
    "qwen": {
        "id": "qwen",
        "label": "Qwen3.5 (27B)",
        "description": (
            "Tốt cho cuộc họp có nhiều thuật ngữ kỹ thuật phức tạp."
        ),
        "env": {
            "base_url": "QWEN_BASE_URL",
            "api_key": "QWEN_API_KEY",
            "model": "QWEN_MODEL",
        },
        "context_chars": 14_000,
        "max_tokens": 8_000,
    },
    "gpt-oss": {
        "id": "gpt-oss",
        "label": "GPT-OSS (120B)",
        "description": (
            "Chính xác cao nhất cho cuộc họp quan trọng — chậm hơn."
        ),
        "env": {
            "base_url": "GPT_OSS_BASE_URL",
            "api_key": "GPT_OSS_API_KEY",
            "model": "GPT_OSS_MODEL",
        },
        "context_chars": 60_000,
        "max_tokens": 16_000,
    },
}

DEFAULT_STT = "whisper"
DEFAULT_LLM = "gemma"


def _is_profile_configured(env_keys: dict) -> bool:
    """True when the profile's base_url + model are both set. api_key is
    shared (LLM_API_KEY / WHISPER_API_KEY) so it's NOT a per-profile setup
    concern — same MaaS account key works for every model under it."""
    return bool(os.getenv(env_keys["base_url"])) and bool(os.getenv(env_keys["model"]))


def _resolve_env(env_keys: dict, legacy_prefix: str, profile_id: str) -> dict:
    """Read base_url + model from profile-specific vars; api_key is shared
    via the legacy LLM_API_KEY / WHISPER_API_KEY (one MaaS key per kind).
    Logs a clear warning if base_url/model fall back to legacy — usually
    means user picked a profile but didn't add its URL+model to .env."""
    base_url = os.getenv(env_keys["base_url"])
    model = os.getenv(env_keys["model"])
    # api_key: optional per-profile override (rare — different MaaS account)
    # → otherwise the shared legacy key.
    api_key = (
        os.getenv(env_keys["api_key"])
        or os.getenv(f"{legacy_prefix}_API_KEY", "")
    )
    fallback_used = []
    if not base_url:
        base_url = os.getenv(f"{legacy_prefix}_BASE_URL", "")
        fallback_used.append(env_keys["base_url"])
    if not model:
        model = os.getenv(f"{legacy_prefix}_MODEL", "")
        fallback_used.append(env_keys["model"])
    if fallback_used:
        logger.warning(
            f"[model_registry] profile '{profile_id}' missing {fallback_used} "
            f"— falling back to {legacy_prefix}_*. Set these in .env to "
            f"actually use '{profile_id}'."
        )
    return {"base_url": base_url, "api_key": api_key, "model": model}


def get_profiles(kind: str) -> list[dict]:
    """Return UI-friendly list of profiles for STT or LLM dropdowns.
    Each entry has id, label, description, configured. `configured=false`
    means the FE should disable / badge the option since picking it won't
    actually switch the backend (resolver falls back to legacy default)."""
    source = STT_PROFILES if kind == "stt" else LLM_PROFILES
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "description": p["description"],
            "configured": _is_profile_configured(p["env"]),
        }
        for p in source.values()
    ]


def resolve_stt(
    recording_choice: Optional[str] = None,
    meeting_choice: Optional[str] = None,
) -> dict:
    """Resolve effective STT profile for a recording.
    Priority: recording → meeting (project default) → DEFAULT_STT."""
    choice = recording_choice or meeting_choice or DEFAULT_STT
    if choice not in STT_PROFILES:
        choice = DEFAULT_STT
    profile = STT_PROFILES[choice].copy()
    profile.update(_resolve_env(profile["env"], "WHISPER", choice))
    return profile


def resolve_llm(
    recording_choice: Optional[str] = None,
    meeting_choice: Optional[str] = None,
) -> dict:
    """Resolve effective LLM profile for a recording.
    Priority: recording → meeting (project default) → DEFAULT_LLM."""
    choice = recording_choice or meeting_choice or DEFAULT_LLM
    if choice not in LLM_PROFILES:
        choice = DEFAULT_LLM
    profile = LLM_PROFILES[choice].copy()
    profile.update(_resolve_env(profile["env"], "LLM", choice))
    return profile
