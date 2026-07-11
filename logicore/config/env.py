"""
Single gateway to environment configuration for the entire Logicore framework.

Design invariant
----------------
`logicore.config` is the ONLY package permitted to read `os.environ` for
configuration purposes. The user owns a single `.env` file (repo root); this
module loads it exactly once via `python-dotenv` and exposes thin, typed
accessors. Every other module should import typed settings from
`logicore.config` and must never call `os.getenv` / `os.environ` directly.

Precedence (framework-wide): explicit constructor arg > ENV (.env) > default.
"""
import os
import warnings
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load the user's .env exactly once. Idempotent across imports.
load_dotenv()

# New variable name -> deprecated old name. When the new name is unset but the
# old one is present, we honor it (with a DeprecationWarning) for one release.
_DEPRECATED_ALIASES = {
    "LOGICORE_STORAGE_ROOT": "STORAGE_ROOT",
}


def _raw(key: str, default: Optional[str] = None) -> Optional[str]:
    """The ONLY direct ``os.environ`` read in the framework.

    Honors deprecated aliases (e.g. ``STORAGE_ROOT``) by falling back to the
    new name and emitting a ``DeprecationWarning`` on first hit.
    """
    value = os.getenv(key)
    if value is not None:
        return value
    if key in _DEPRECATED_ALIASES:
        new_key = _DEPRECATED_ALIASES[key]
        value = os.getenv(new_key)
        if value is not None:
            warnings.warn(
                f"Environment variable '{key}' is deprecated; use '{new_key}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            return value
    return default


def _expand(path: str) -> Path:
    """Expand ``~`` and ``$VAR`` references in a path string."""
    return Path(os.path.expanduser(os.path.expandvars(path)))


def resolve(explicit, env_name: str, default):
    """Precedence helper used framework-wide: explicit > ENV > default."""
    if explicit is not None:
        return explicit
    value = _raw(env_name)
    return value if value is not None else default


def storage_root() -> Path:
    """Resolve the master storage root (all persisted state lives under it)."""
    return _expand(
        _raw("LOGICORE_STORAGE_ROOT")
        or _raw("STORAGE_ROOT")
        or str(Path.home() / ".logicore")
    )
