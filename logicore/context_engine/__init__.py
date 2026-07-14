"""
Context Engine — Backward-compatibility barrel.

ALL context management logic now lives in logicore.runtime.context.
This package re-exports everything for backward compatibility.

New code should import from logicore.runtime.context directly.
"""

from logicore.runtime.context import *  # noqa: F401,F403
from logicore.runtime.context import __all__  # noqa: F401
