"""Pluggable model registry so the UI/user can switch chatbots.

Includes paid (Anthropic Claude) and free/open-source options (Groq-hosted Llama,
local Ollama). A model is usable when its provider package is importable and its
API key (if any) is set — `available_models()` reports this so the UI can show
only working choices.

Resolution returns a `provider:model` spec string; create_deep_agent /
init_chat_model build the actual chat model (and require tool-calling support).
"""
from __future__ import annotations

import os

# id -> (spec, label, env key needed or None for keyless/local)
MODEL_REGISTRY: dict[str, dict] = {
    "claude-sonnet-4-6": {
        "spec": "anthropic:claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6 — Anthropic (recommended)",
        "needs_key": "ANTHROPIC_API_KEY",
    },
    "claude-haiku-4-5": {
        "spec": "anthropic:claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5 — Anthropic (fast/cheap)",
        "needs_key": "ANTHROPIC_API_KEY",
    },
    "groq-llama-3.3-70b": {
        "spec": "groq:llama-3.3-70b-versatile",
        "label": "Llama 3.3 70B — Groq (free tier, open weights)",
        "needs_key": "GROQ_API_KEY",
    },
    "groq-gpt-oss-20b": {
        "spec": "groq:openai/gpt-oss-20b",
        "label": "GPT-OSS 20B — Groq (free tier, open weights)",
        "needs_key": "GROQ_API_KEY",
    },
    "ollama-qwen2.5": {
        "spec": "ollama:qwen2.5",
        "label": "Qwen2.5 — Ollama (local, free, no key)",
        "needs_key": None,
    },
    "ollama-llama3.1": {
        "spec": "ollama:llama3.1",
        "label": "Llama 3.1 8B — Ollama (local, free, no key)",
        "needs_key": None,
    },
}

DEFAULT_MODEL_ID = os.environ.get("AGENT_MODEL_ID", "claude-sonnet-4-6")


def resolve_spec(model_id: str | None = None) -> str:
    """Map a registry id to a provider:model spec.

    Unknown ids are passed through verbatim so a raw `provider:model` spec also
    works (e.g. AGENT_MODEL_ID='openai:gpt-4.1-mini')."""
    model_id = model_id or DEFAULT_MODEL_ID
    entry = MODEL_REGISTRY.get(model_id)
    return entry["spec"] if entry else model_id


def is_available(entry: dict) -> bool:
    key = entry["needs_key"]
    return key is None or bool(os.environ.get(key))


def available_models() -> list[dict]:
    """Models whose key is configured (Ollama/local always listed). For the UI."""
    out = []
    for mid, entry in MODEL_REGISTRY.items():
        out.append({
            "id": mid,
            "label": entry["label"],
            "available": is_available(entry),
            "needs_key": entry["needs_key"],
        })
    return out
