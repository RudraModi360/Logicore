"""
TokenEstimator: Centralized token counting for the context engine.

Replaces ad-hoc `chars // 4` estimates scattered across the codebase.
Provides a single source of truth for token measurement.

Two modes (both produce a PRE-REQUEST estimate; the ACTUAL tokens always
come from the provider-reported usage on the response):

* Legacy (zero-arg): tiktoken cl100k when available, else ~4 chars/token.
  Unchanged behavior for existing callers.
* Provider-aware: pass provider= + model= to get a counter tuned to that
  provider's tokenizer. Fixed providers (openai/azure/anthropic/gemini/
  groq/ollama) are accurate; custom endpoints try HF -> tiktoken -> heuristic
  and always surface an inaccuracy warning on fallback.

Usage truth note: for the six fixed providers the response usage is the
source of truth for input/output/cache/reasoning tokens. This estimator only
sizes the request BEFORE the call (compression/masking/budget decisions).
"""

from __future__ import annotations

import json
import logging
import math
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# === Optional tiktoken import ===
try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

# === Model context windows ===
MODEL_CONTEXT_WINDOWS = {
    # OpenAI
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 128000,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3": 200000,
    # Anthropic
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-4-opus": 200000,
    # Google
    "gemini-pro": 32000,
    "gemini-1.5-pro": 1000000,
    "gemini-1.5-flash": 1000000,
    "gemini-2.5-pro": 1000000,
    "gemini-2.5-flash": 1000000,
    # Meta
    "llama3": 8192,
    "llama3.1": 128000,
    "llama3.2": 128000,
    "llama-3": 8192,
    "llama-3.1": 128000,
    "llama-3.2": 128000,
    # Mistral
    "mistral": 8192,
    "mixtral": 32768,
    "mistral-large": 128000,
    # Qwen
    "qwen": 32768,
    "qwen-2.5": 128000,
    "qwen3": 128000,
    "qwen3:0.6b": 32768,
    # DeepSeek
    "deepseek-chat": 128000,
    "deepseek-coder": 128000,
    "deepseek-v2": 128000,
    "deepseek-v3": 128000,
    "deepseek-v4": 128000,
    # Ollama / Local
    "gpt-oss": 128000,
    "gpt-oss:20b-cloud": 128000,
    # Fallback
    "default": 256000,
}


def get_model_context_window(model_name: str, provider=None) -> int:
    """
    Get context window size for a model.
    Tries: provider.get_context_window() → exact match → prefix match → contains match → default.
    """
    # Check if provider has explicit context window setting
    if provider and hasattr(provider, 'get_context_window'):
        ctx = provider.get_context_window()
        if ctx and ctx > 0:
            return ctx
    
    if model_name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_name]
    
    # Prefix match (e.g., "gpt-4o-2024-01" matches "gpt-4o")
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if model_name.startswith(known):
            return window
    
    # Contains match (e.g., "deepseek-v4-flash-free" contains "deepseek-v4")
    model_lower = model_name.lower()
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        if known.lower() in model_lower:
            return window
    
    # Partial prefix match for versioned models (e.g., "deepseek-v4" matches "deepseek-v3")
    for known, window in MODEL_CONTEXT_WINDOWS.items():
        known_parts = known.lower().split("-")
        model_parts = model_lower.split("-")
        # Check if first 2 parts match (e.g., "deepseek-v" matches)
        if len(known_parts) >= 2 and len(model_parts) >= 2:
            if known_parts[0] == model_parts[0] and known_parts[1] == model_parts[1]:
                return window
    
    return MODEL_CONTEXT_WINDOWS["default"]


def get_tiktoken_counter(encoding_name: str = "cl100k_base") -> Optional[Callable[[str], int]]:
    """Get a tiktoken-based token counter.
    
    Returns a Callable[[str], int] that counts tokens using BPE encoding.
    Returns None if tiktoken is not installed.
    
    Args:
        encoding_name: tiktoken encoding name. Default "cl100k_base" covers
                       GPT-4, GPT-3.5-Turbo, and Claude models.
    """
    if not _TIKTOKEN_AVAILABLE:
        return None
    
    try:
        enc = tiktoken.get_encoding(encoding_name)
        
        def _count_tokens(text: str) -> int:
            if not text:
                return 0
            return len(enc.encode(text))
        
        return _count_tokens
    except Exception as e:
        logger.warning(f"[TokenEstimator] Failed to load tiktoken encoding '{encoding_name}': {e}")
        return None


def estimate_tokens(text: str) -> int:
    """Estimate token count from text (~4 chars per token)."""
    if not text:
        return 0
    return len(text) // 4


def estimate_message_tokens(messages: list) -> int:
    """Estimate tokens for a list of messages."""
    total = 0
    for msg in messages:
        total += 4  # Role overhead
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += estimate_tokens(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        total += 1000
        if "tool_calls" in msg:
            total += estimate_tokens(str(msg["tool_calls"]))
    return total


class TokenEstimator:
    """
    Estimates token counts for messages and text.

    Zero-arg (BC): tiktoken BPE when available, else ~4 chars/token.
    Provider-aware: TokenEstimator(provider="openai", model="gpt-4o") picks a
    counter tuned to that provider. An explicit token_counter always wins.

    The count returned is a PRE-REQUEST estimate. Actual input/output/cache/
    reasoning tokens are reported by the provider on the response and
    normalized via logicore.telemetry.canonical.normalize_usage.
    """

    # Per-message role/template overhead (ChatML-style wrap ~4 special tokens).
    _MSG_OVERHEAD = {"gemini": 1, "anthropic": 3}
    _DEFAULT_MSG_OVERHEAD = 4
    # Conversation priming overhead (OpenAI-style +3; Gemini ~+1).
    _CONV_OVERHEAD = {"gemini": 1, "openai": 3, "azure": 3}
    _DEFAULT_CONV_OVERHEAD = 1

    # Heuristic chars-per-token by content type (calibrated).
    _PROSE_CPT = 4.0
    _CODE_CPT = 3.2
    _JSON_CPT = 2.8
    _CODE_MARKERS = ("def ", "function", "return ", "=>", "->", "import ", "class ")
    _JSON_PUNCT = set('{}",:[]')

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
        provider: str = "unknown",
        model: str = "default",
    ):
        self.provider = (provider or "unknown").lower()
        self.model = model or "default"
        self._explicit = token_counter
        self.warnings: List[str] = []
        self._accuracy = "estimated"
        if self._explicit is not None:
            self._counter = self._explicit
            self._accuracy = "exact"
        else:
            self._counter = self._build_counter()

    # ------------------------------------------------------------------ builders
    def _build_counter(self) -> Callable[[str], int]:
        p = self.provider
        if p in ("openai", "azure"):
            return self._tiktoken_for_model(self.model)
        if p == "anthropic" or "claude" in self.model.lower():
            self.warnings.append(
                "Anthropic tokenizer unavailable; using tiktoken cl100k approximation (~3% error)"
            )
            self._accuracy = "estimated"
            return get_tiktoken_counter("cl100k_base") or self._default_counter
        if p == "gemini":
            self.warnings.append(
                "Gemini pre-count uses a calibrated heuristic; actual tokens are reported by the API"
            )
            return self._heuristic_counter("gemini")
        if p in ("groq", "ollama"):
            # Llama-family BPE is close to cl100k; actual tokens come from usage.
            self._accuracy = "medium"
            return get_tiktoken_counter("cl100k_base") or self._default_counter
        if p == "custom":
            return self._custom_counter()
        # unknown -> legacy behavior
        tc = get_tiktoken_counter()
        self._accuracy = "high" if tc else "estimated"
        return tc or self._default_counter

    def _tiktoken_for_model(self, model: str) -> Callable[[str], int]:
        if not _TIKTOKEN_AVAILABLE:
            self.warnings.append("tiktoken unavailable; falling back to ~4 chars/token")
            self._accuracy = "estimated"
            return self._default_counter
        name = (model or "").lower()
        if name.startswith(("gpt-4o", "gpt-4.1", "o3", "o4")):
            self._accuracy = "exact"
            return get_tiktoken_counter("o200k_base") or self._default_counter
        if name.startswith(("gpt-3.5-turbo-instruct", "davinci")):
            self._accuracy = "exact"
            return get_tiktoken_counter("p50k_base") or self._default_counter
        if name.startswith(("gpt-4", "gpt-3.5", "o1", "text-embedding")):
            self._accuracy = "exact"
            return get_tiktoken_counter("cl100k_base") or self._default_counter
        try:
            enc = tiktoken.encoding_for_model(model)
            self._accuracy = "exact"
            return self._wrap_encoding(enc)
        except Exception:
            self.warnings.append(
                "Model not in tiktoken registry — fell back to cl100k_base (high, not exact)"
            )
            self._accuracy = "high"
            return get_tiktoken_counter("cl100k_base") or self._default_counter

    @staticmethod
    def _wrap_encoding(enc) -> Callable[[str], int]:
        def _count(text: str) -> int:
            if not text:
                return 0
            return len(enc.encode(text, disallowed_special=()))
        return _count

    @staticmethod
    def _default_counter(text: str) -> int:
        """Ultimate fallback: ~4 characters per token."""
        if not text:
            return 0
        return len(text) // 4

    def _heuristic_counter(self, provider: str) -> Callable[[str], int]:
        # Gemini multiplier is 1.0 (SentencePiece calibration handled via overhead).
        def _count(text: str) -> int:
            if not text:
                return 0
            ratio = self._detect_ratio(text)
            return max(1, math.ceil(len(text) / ratio))
        self._accuracy = "estimated"
        return _count

    def _custom_counter(self) -> Callable[[str], int]:
        """Custom endpoints: try HF (from model name) -> tiktoken -> heuristic.

        Mirrors the experiment's approach: no static model->tokenizer map; load
        HF AutoTokenizer straight from the model name when possible. Always
        warns when the count may be inaccurate.
        """
        # 1. HF AutoTokenizer from the model name (lazy, cached, guarded).
        hf = self._try_hf_counter(self.model)
        if hf is not None:
            self._accuracy = "high"
            return hf
        # 2. tiktoken cl100k as a reasonable middle ground.
        tc = get_tiktoken_counter("cl100k_base")
        if tc is not None:
            self.warnings.append(
                "Custom endpoint: HF tokenizer unavailable; using tiktoken cl100k — count may be inaccurate"
            )
            self._accuracy = "medium"
            return tc
        # 3. Heuristic fallback — always flagged.
        self.warnings.append(
            "Custom endpoint: count may be inaccurate — heuristic fallback (~4 chars/token) only"
        )
        self._accuracy = "estimated"
        return self._default_counter

    @staticmethod
    def _try_hf_counter(model: str) -> Optional[Callable[[str], int]]:
        if not model or model == "default":
            return None
        try:
            from transformers import AutoTokenizer
        except Exception:
            return None
        try:
            tok = AutoTokenizer.from_pretrained(model, trust_remote_code=False)
        except Exception:
            return None

        def _count(text: str) -> int:
            if not text:
                return 0
            try:
                return len(tok.encode(text, add_special_tokens=False))
            except Exception:
                return max(1, len(text) // 4)
        return _count

    @staticmethod
    def _detect_ratio(text: str) -> float:
        stripped = text.strip()
        if stripped[:1] in ("{", "["):
            punct = sum(1 for c in text if c in TokenEstimator._JSON_PUNCT)
            if punct / max(len(text), 1) > 0.08:
                return TokenEstimator._JSON_CPT
            return TokenEstimator._CODE_CPT
        lowered = text.lower()
        hits = sum(1 for m in TokenEstimator._CODE_MARKERS if m in lowered)
        if hits >= 2 or text.count("\n    ") >= 3:
            return TokenEstimator._CODE_CPT
        return TokenEstimator._PROSE_CPT

    # ------------------------------------------------------------------ counting
    def count_tokens(self, text: str) -> int:
        """Count tokens in a string (pre-request estimate)."""
        return self._counter(text or "")

    def count_tool_tokens(self, tool_defs: List[Dict[str, Any]]) -> int:
        """Count tool schema tokens as compact JSON."""
        if not tool_defs:
            return 0
        blob = json.dumps(tool_defs, separators=(",", ":"), ensure_ascii=False)
        return self._counter(blob)

    def _msg_overhead(self, msg: Dict[str, Any]) -> int:
        if not msg.get("role"):
            return 0
        return self._MSG_OVERHEAD.get(self.provider, self._DEFAULT_MSG_OVERHEAD)

    def count_message_tokens(self, msg: Dict[str, Any]) -> int:
        """Estimate tokens in a single message (content + role overhead)."""
        total = self._msg_overhead(msg)
        content = msg.get("content", "")

        if isinstance(content, str):
            total += self._counter(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if "text" in part:
                        total += self._counter(part["text"])
                    elif part.get("type") == "image_url":
                        total += 1000
                elif isinstance(part, str):
                    total += self._counter(part)

        if "tool_calls" in msg:
            total += self._counter(json.dumps(msg["tool_calls"], default=str))

        return total

    def count_messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Estimate total tokens across a list of messages (incl. priming overhead)."""
        total = sum(self.count_message_tokens(m) for m in messages)
        if messages:
            total += self._CONV_OVERHEAD.get(self.provider, self._DEFAULT_CONV_OVERHEAD)
        return total

    def categorize_tokens(
        self, messages: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Categorize tokens by type.

        Returns dict with keys: system, tools, tool_results, messages.
        """
        categories = {"system": 0, "tools": 0, "tool_results": 0, "messages": 0}

        for msg in messages:
            role = msg.get("role", "")
            tokens = self.count_message_tokens(msg)

            if role == "system":
                categories["system"] += tokens
            elif role == "tool":
                categories["tool_results"] += tokens
            else:
                categories["messages"] += tokens

        return categories

    # ------------------------------------------------------------------ introspection
    def get_accuracy_class(self) -> str:
        """How much to trust this estimator's pre-request numbers."""
        return self._accuracy

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "accuracy_class": self._accuracy,
            "warnings": list(self.warnings),
        }
