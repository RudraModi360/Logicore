"""
Verifier registry.

Auto-discovers and caches type-specific verifiers. Falls back to
GenericVerifier when no specific verifier matches.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from logicore.verification.base_verifier import BaseVerifier
from logicore.verification.verifiers.generic import GenericVerifier

logger = logging.getLogger(__name__)


class VerifierRegistry:
    """Registry that maps artifact types to verifier instances.

    Verifiers are lazily loaded on first access.  The GenericVerifier
    is always registered as the fallback.

    Usage::

        registry = VerifierRegistry()
        verifier = registry.get_verifier("pptx")
        result = verifier.verify("/path/to/file.pptx")
    """

    def __init__(self) -> None:
        self._cache: Dict[str, BaseVerifier] = {}
        self._generic = GenericVerifier()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load all built-in verifiers."""
        if self._loaded:
            return
        self._loaded = True

        # Import built-in verifiers.  Each module exposes a
        # ``get_verifier()`` function returning a BaseVerifier instance.
        _builtin_modules = [
            ("logicore.verification.verifiers.generic", "get_verifier"),
            ("logicore.verification.verifiers.image", "get_verifier"),
            ("logicore.verification.verifiers.pdf", "get_verifier"),
            ("logicore.verification.verifiers.docx", "get_verifier"),
            ("logicore.verification.verifiers.pptx", "get_verifier"),
            ("logicore.verification.verifiers.xlsx", "get_verifier"),
            ("logicore.verification.verifiers.html", "get_verifier"),
        ]

        for module_path, factory_name in _builtin_modules:
            try:
                import importlib
                mod = importlib.import_module(module_path)
                factory = getattr(mod, factory_name, None)
                if factory:
                    verifier = factory()
                    self.register(verifier)
            except Exception as exc:
                logger.debug(f"Could not load verifier {module_path}: {exc}")

    def register(self, verifier: BaseVerifier) -> None:
        """Register a verifier for its supported extensions."""
        for ext in verifier.supported_extensions():
            existing = self._cache.get(ext)
            if existing and type(existing) is not GenericVerifier:
                logger.debug(
                    f"Overriding verifier for {ext}: "
                    f"{type(existing).__name__} -> {type(verifier).__name__}"
                )
            self._cache[ext] = verifier

    def get_verifier(self, artifact_type: str) -> BaseVerifier:
        """Return the verifier for *artifact_type*, or GenericVerifier."""
        self._ensure_loaded()

        ext = f".{artifact_type}" if not artifact_type.startswith(".") else artifact_type
        ext = ext.lower()

        return self._cache.get(ext, self._generic)

    def has_verifier(self, artifact_type: str) -> bool:
        """Return True if a type-specific (non-generic) verifier exists."""
        self._ensure_loaded()

        ext = f".{artifact_type}" if not artifact_type.startswith(".") else artifact_type
        ext = ext.lower()

        verifier = self._cache.get(ext)
        return verifier is not None and verifier is not self._generic

    def list_verifiers(self) -> Dict[str, str]:
        """Return a mapping of extension -> verifier class name."""
        self._ensure_loaded()

        result: Dict[str, str] = {}
        for ext, verifier in self._cache.items():
            result[ext] = type(verifier).__name__
        return result


# Module-level convenience instance.
_registry: Optional[VerifierRegistry] = None


def get_registry() -> VerifierRegistry:
    """Return the global VerifierRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = VerifierRegistry()
    return _registry
