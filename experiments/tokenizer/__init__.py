"""Provider-aware tokenizer strategies (experiments prototype of TOKEN_SYSTEM_SPEC Component A).

Public API:
    get_tokenizer(provider, model) -> TokenizerStrategy
    ProviderTokenizerRegistry (singleton)
    TokenCountResult, TokenizerStrategy
"""

from .base import TokenCountResult, TokenizerStrategy
from .registry import ProviderTokenizerRegistry, get_tokenizer
from .heuristic_strategy import HeuristicStrategy
from .hf_loader import clear_hf_cache

__all__ = [
    "TokenCountResult",
    "TokenizerStrategy",
    "ProviderTokenizerRegistry",
    "get_tokenizer",
    "HeuristicStrategy",
    "clear_hf_cache",
]
