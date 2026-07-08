"""
Debug logging setup for LogiCore.

The framework emits a lot of ``logger.debug(...)`` calls (gateway requests,
tool calls, orchestration decisions, retries, ...). Out of the box Python's
logging is unconfigured (level WARNING, no handler), so ``debug=True`` on an
agent appears to do nothing. Call :func:`setup_debug_logging` (idempotent) to
attach a stream handler and lower the level so those traces become visible.
"""

import logging
import sys

_CONFIGURED = False

# Components to surface at DEBUG when debug mode is on. Broadening this list
# gives finer-grained tracing without flooding unrelated libraries.
_DEBUG_LOGGERS = [
    "logicore.agent",
    "logicore.gateway",
    "logicore.providers",
    "logicore.tools",
    "logicore.memory",
    "logicore.runtime",
    "logicore.skills",
]


def setup_debug_logging(level: int = logging.DEBUG) -> None:
    """
    Configure root logging so DEBUG traces from LogiCore components are
    printed to stderr. Safe to call multiple times (only configures once).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    # Avoid clobbering an already-configured handler setup by the host app.
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.setLevel(level)
    else:
        # Handlers exist; just make sure DEBUG isn't suppressed.
        root.setLevel(min(root.level, level))

    for name in _DEBUG_LOGGERS:
        logging.getLogger(name).setLevel(level)

    # Quiet down chatty third-party network libraries so debug output stays
    # focused on LogiCore's own traces (gateway/tool/orchestration/etc.).
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
