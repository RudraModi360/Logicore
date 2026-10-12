"""Google Gemini strategy — dual mode.

Mode 1 (sync):  calibrated heuristic, used for pre-request budget decisions.
                exactness="estimated". Warns when images/audio are present
                because the heuristic cannot see multimodal content.

Mode 2 (async): wraps the native client.models.count_tokens() call.
                exactness="provider_reported". Note: Gemini's countTokens API
                rejects a `tools` config — tool schemas fall back to the
                heuristic with a warning (verified live 2026).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .base import TokenCountResult, TokenizerStrategy, _message_text
from .heuristic_strategy import HeuristicStrategy

GEMINI_WARNING = "Gemini tokenizer unavailable; using calibrated heuristic (~5-10% error)"
MULTIMODAL_WARNING = "Multimodal content present — sync heuristic cannot account for images/audio"
TOOLS_FALLBACK_WARNING = (
    "Gemini countTokens API rejects tools config — tool schema counted via heuristic"
)


class GoogleStrategy(TokenizerStrategy):
    """Gemini counting: sync heuristic + optional async native countTokens."""

    provider = "gemini"

    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = model
        self._heuristic = HeuristicStrategy(provider="gemini", model=model)

    @property
    def accuracy_class(self) -> str:
        return "estimated"

    def _has_multimodal(self, messages: List[Dict[str, Any]]) -> bool:
        for msg in messages or []:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") not in (None, "text"):
                        return True
                    if not isinstance(part, dict):
                        return True
        return False

    def count(self, text: str) -> int:
        return self._heuristic.count(text)

    def count_message(self, msg: Dict[str, Any]) -> int:
        return self._heuristic.count_message(msg) + 2

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = sum(self.count_message(m) for m in (messages or []))
        return total + 1

    def count_messages_result(self, messages: List[Dict[str, Any]]) -> TokenCountResult:
        """Sync count wrapped in a TokenCountResult (for budget decisions)."""
        warnings = [GEMINI_WARNING]
        if self._has_multimodal(messages):
            warnings.append(MULTIMODAL_WARNING)
        return TokenCountResult(
            input_tokens=self.count_messages(messages),
            method="heuristic",
            exactness="estimated",
            model=self.model,
            warnings=warnings,
        )

    async def count_async(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        client=None,
        model_name: str = "",
    ) -> TokenCountResult:
        """Native Gemini countTokens API. Returns provider_reported exactness."""
        model = model_name or self.model
        warnings: List[str] = []
        if client is None:
            try:
                from google import genai
                import os

                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
            except Exception as e:
                return TokenCountResult(
                    input_tokens=None,
                    method="unknown",
                    exactness="unknown",
                    model=model,
                    warnings=[f"Gemini client unavailable for countTokens: {e}"],
                )

        contents = self._to_contents(messages)
        config = None
        if tools:
            warnings.append(TOOLS_FALLBACK_WARNING)

        def _call():
            return client.models.count_tokens(model=model, contents=contents, config=config)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, _call)
        except Exception as e:
            return TokenCountResult(
                input_tokens=None,
                method="unknown",
                exactness="unknown",
                model=model,
                warnings=[f"countTokens call failed: {e}"],
            )

        total = getattr(response, "total_tokens", None)
        tool_tokens = 0
        if tools:
            tool_tokens = HeuristicStrategy(provider="gemini").count_tools(tools)
        final = (total or 0) + tool_tokens
        return TokenCountResult(
            input_tokens=final,
            method="native_api",
            exactness="provider_reported",
            model=model,
            warnings=warnings,
        )

    @staticmethod
    def _to_contents(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style messages to Gemini contents (role + parts)."""
        contents = []
        for msg in messages or []:
            role = msg.get("role", "user")
            if role == "system":
                role = "user"
            text = _message_text(msg)
            contents.append({"role": role, "parts": [{"text": text}]})
        return contents
