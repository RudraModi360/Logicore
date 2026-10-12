"""Shared lazy HuggingFace tokenizer loading + model-family routing.

HF tokenizer loads hit disk/network on first use, so they are loaded on
first count per model ID and cached on the process. Gated repos (e.g.
meta-llama/*) and offline environments raise OSError/EnvironmentError —
callers must catch and fall back to HeuristicStrategy.
"""

from __future__ import annotations

from typing import Dict, Optional

_TOKENIZER_CACHE: Dict[str, object] = {}

GATED_OR_OFFLINE_WARNING = (
    "HF tokenizer unavailable (gated repo or offline) — fell back to heuristic"
)


def load_hf_tokenizer(model_id: str, revision: str = "main"):
    """Load + cache an HF tokenizer. Returns None on any failure."""
    if model_id in _TOKENIZER_CACHE:
        return _TOKENIZER_CACHE[model_id]
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            model_id, revision=revision, trust_remote_code=False
        )
        _TOKENIZER_CACHE[model_id] = tokenizer
        return tokenizer
    except Exception:
        _TOKENIZER_CACHE[model_id] = None
        return None


def clear_hf_cache() -> None:
    _TOKENIZER_CACHE.clear()


def count_with_chat_template(tokenizer, messages) -> Optional[int]:
    """Count a conversation via apply_chat_template (adds real special tokens).

    Returns None when the tokenizer/template rejects the input.
    """
    try:
        ids = tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True
        )
        return len(ids)
    except Exception:
        return None


def count_text_with_tokenizer(tokenizer, text: str) -> int:
    try:
        return len(tokenizer.encode(text, add_special_tokens=False))
    except Exception:
        try:
            return len(tokenizer.encode(text))
        except Exception:
            return max(1, len(text) // 4)


GROQ_FAMILY_MAP = [
    ("llama-3.1", "meta-llama/Llama-3.1-8B"),
    ("llama-3.2", "meta-llama/Llama-3.1-8B"),
    ("llama-3.3", "meta-llama/Llama-3.1-8B"),
    ("llama3", "meta-llama/Llama-3.1-8B"),
    ("llama-3", "meta-llama/Meta-Llama-3-8B"),
    ("mixtral", "mistralai/Mixtral-8x7B-v0.1"),
    ("gemma", "google/gemma-7b"),
]

OLLAMA_FAMILY_MAP = [
    ("llama3", "meta-llama/Llama-3.1-8B"),
    ("llama-3", "meta-llama/Llama-3.1-8B"),
    ("qwen3", "Qwen/Qwen2.5-7B"),
    ("qwen2", "Qwen/Qwen2.5-7B"),
    ("qwen", "Qwen/Qwen2.5-7B"),
    ("mistral", "mistralai/Mistral-7B-v0.1"),
    ("gemma", "google/gemma-7b"),
    ("phi", "microsoft/phi-2"),
    ("deepseek", "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"),
]


def resolve_family(model: str, family_map) -> Optional[str]:
    name = (model or "").lower()
    for prefix, hf_id in family_map:
        if name.startswith(prefix):
            return hf_id
    return None
